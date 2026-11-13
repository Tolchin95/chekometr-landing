#!/usr/bin/env python3
"""Обновляет assets/official-inflation.json по таблице Банка России «Инфляция и ключевая ставка».

Запускается ежедневно из .github/workflows/update-inflation.yml. Только стандартная библиотека.

Коды выхода:
  0 — JSON обновлён новым месяцем или в таблице нет месяца новее текущего (тогда файл не трогаем);
  1 — таблицу не удалось получить или разобрать, либо значение не прошло проверки (JSON не тронут).

Если задана переменная GITHUB_OUTPUT, пишет туда changed=true|false и commit_message.

Для локальной проверки: --html-file подставляет сохранённую страницу вместо запроса к cbr.ru,
--json указывает другой JSON, --today задаёт «сегодня» (ГГГГ-ММ-ДД).
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_JSON = os.path.join(REPO_ROOT, "assets", "official-inflation.json")

SOURCE_URL = "https://www.cbr.ru/hd_base/infl/"
SOURCE_NAME = "Банк России (данные Росстата)"
QUERY_URL = SOURCE_URL + "?UniDbQuery.Posted=True&UniDbQuery.From={from_}&UniDbQuery.To={to}"
USER_AGENT = "Mozilla/5.0 (compatible; chekometr-inflation-updater; +https://chekometr.ru)"

DATE_HEADER = "Дата"
INFLATION_HEADER = "Инфляция, % г/г"
MIN_VALUE, MAX_VALUE = 0.0, 30.0
EMPTY_CELLS = {"", "-", "—", "–"}

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


class UpdateError(Exception):
    """Таблица не получена или не разобрана — JSON трогать нельзя."""


class TableParser(HTMLParser):
    """Собирает все таблицы страницы как списки строк из текстов ячеек (th и td)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def fetch_html(today):
    start = today - dt.timedelta(days=400)
    url = QUERY_URL.format(from_=start.strftime("%d.%m.%Y"), to=today.strftime("%d.%m.%Y"))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_err = err
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise UpdateError("не удалось скачать {}: {}".format(url, last_err))


def parse_number(text):
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise UpdateError("значение инфляции не число: {!r}".format(text))
    return float(text)


def latest_row(html):
    """Возвращает (period 'ГГГГ-ММ', value) для самого позднего месяца с заполненной инфляцией."""
    parser = TableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        header = table[0]
        if DATE_HEADER not in header or INFLATION_HEADER not in header:
            continue
        date_i, infl_i = header.index(DATE_HEADER), header.index(INFLATION_HEADER)
        rows = []
        for row in table[1:]:
            if len(row) <= max(date_i, infl_i):
                raise UpdateError("в строке таблицы не хватает столбцов: {!r}".format(row))
            m = re.fullmatch(r"(\d{2})\.(\d{4})", row[date_i])
            if not m:
                raise UpdateError("неожиданный формат даты: {!r}".format(row[date_i]))
            month, year = int(m.group(1)), int(m.group(2))
            if not 1 <= month <= 12:
                raise UpdateError("неверный месяц: {!r}".format(row[date_i]))
            cell = row[infl_i].strip()
            # ЦБ может заранее добавить строку месяца, где есть ставка, но ещё нет инфляции.
            if cell in EMPTY_CELLS:
                continue
            rows.append(("{:04d}-{:02d}".format(year, month), parse_number(cell)))
        if not rows:
            raise UpdateError("в таблице нет строк с инфляцией")
        return max(rows)
    raise UpdateError("не найдена таблица со столбцами «{}» и «{}»".format(DATE_HEADER, INFLATION_HEADER))


def read_current(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        period = data["period"]
    except (OSError, ValueError, KeyError) as err:
        raise UpdateError("не удалось прочитать текущий {}: {}".format(path, err))
    if not re.fullmatch(r"\d{4}-\d{2}", str(period)):
        raise UpdateError("в текущем JSON неверный period: {!r}".format(period))
    return data


def month_label(period):
    year, month = period.split("-")
    return "за {} {}".format(MONTHS[int(month) - 1], year)


def write_json_atomic(path, data):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".official-inflation.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def set_output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, value in values.items():
            f.write("{}={}\n".format(key, value))


def run(args):
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    current = read_current(args.json)

    if args.html_file:
        with open(args.html_file, encoding="utf-8") as f:
            html = f.read()
    else:
        html = fetch_html(today)

    period, value = latest_row(html)

    if not (MIN_VALUE < value < MAX_VALUE):
        raise UpdateError("значение {} вне допустимого диапазона ({}; {})".format(value, MIN_VALUE, MAX_VALUE))
    if period > today.strftime("%Y-%m"):
        raise UpdateError("период {} позже текущего месяца".format(period))

    if period <= current["period"]:
        print("Новых данных нет: в таблице {} ({}%), в JSON {}.".format(period, value, current["period"]))
        set_output(changed="false")
        return 0

    data = {
        "value": round(value, 2),
        "period": period,
        "label": "годовая, " + month_label(period),
        "sourceName": SOURCE_NAME,
        "sourceUrl": SOURCE_URL,
        "checkedAt": today.isoformat(),
    }
    write_json_atomic(args.json, data)

    value_ru = "{:.2f}".format(value).replace(".", ",")
    year, month = period.split("-")
    message = "Update official inflation: {}% за {}.{}".format(value_ru, month, year)
    print("Обновлено: {} → {} ({}%).".format(current["period"], period, value_ru))
    set_output(changed="true", commit_message=message)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=DEFAULT_JSON, help="путь к official-inflation.json")
    ap.add_argument("--html-file", help="разобрать сохранённую страницу вместо запроса к cbr.ru")
    ap.add_argument("--today", help="дата «сегодня» в формате ГГГГ-ММ-ДД (для проверки)")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except UpdateError as err:
        print("Ошибка: {}. JSON не изменён.".format(err), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
