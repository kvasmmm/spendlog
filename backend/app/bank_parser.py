"""Strict parser for the text PDF layout supplied by the user (iM Bank).

NO is an export row number, never a bank transaction identifier.
No OCR guessing: incomplete or unrecognised rows fail the entire preview.
"""
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

PARSER_VERSION = 'im-pdf-layout-v1'
MAX_FILE = 5_000_000
SEOUL = ZoneInfo('Asia/Seoul')
ROW_START = re.compile(r'^\s*(\d+)\s+(\d{4}-\d{2}-\d{2})\s+\[')
ROW = re.compile(r'^\s*(\d+)\s+(\d{4}-\d{2}-\d{2})\s+\[(\d{2}:\d{2}:\d{2})\]\s+([\d,]+)\s+([\d,]+)\s+(-?[\d,]+)(?:\s+(.*))?$')
MONEY = re.compile(r'-?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)\Z')

class StatementError(ValueError):
    pass

@dataclass(frozen=True)
class BankRow:
    number: int
    occurred_at: str
    amount: int
    balance: int
    merchant: str
    raw: str
    precision: str = 'second'

    def identity(self):
        # Bank/account are separate scoping keys. Whitespace in PDF is presentation.
        return [self.occurred_at, self.precision, self.amount, self.balance,
                re.sub(r'\s+', '', self.merchant).casefold()]

    def fingerprint(self):
        return digest(self.identity())

    def data(self):
        return asdict(self)

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def extract_pdf(raw):
    if len(raw) > MAX_FILE:
        raise StatementError('PDF больше 5 MB')
    if not raw.startswith(b'%PDF-'):
        raise StatementError('Нужна текстовая PDF-выписка iM Bank')
    try:
        result = subprocess.run(['pdftotext', '-layout', '-', '-'], input=raw,
                                capture_output=True, timeout=20, check=True)
    except FileNotFoundError as e:
        raise StatementError('На сервере отсутствует pdftotext (poppler-utils)') from e
    except (subprocess.SubprocessError, OSError) as e:
        raise StatementError('Не удалось прочитать PDF; защищённые и сканированные PDF не поддерживаются') from e
    if len(result.stdout) > 5_000_000:
        raise StatementError('Слишком большой текст выписки')
    return result.stdout.decode('utf-8', errors='strict')

def parse_text(text):
    if '거래내역조회' not in text or not all(label in text for label in ('찾으신금액', '맡기신금액', '거래후잔액')):
        raise StatementError('Неизвестный формат PDF: ожидается 거래내역조회 iM Bank')
    account = re.search(r'조회계좌번호\s*:\s*([\d-]+)', text)
    dates = re.search(r'조회기간\s*:\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})', text)
    if not account or not dates:
        raise StatementError('В выписке не найден счёт или период')
    account_number = re.sub(r'\D', '', account[1])
    if len(account_number) < 6:
        raise StatementError('Некорректный номер счёта')
    start, end = map(date.fromisoformat, dates.groups())
    if start > end:
        raise StatementError('Некорректный период')
    rows = []
    for line in text.splitlines():
        if not ROW_START.match(line):
            # A row with a damaged time must not disappear silently.
            if re.match(r'^\s*\d+\s+\d{4}-\d{2}-\d{2}', line):
                raise StatementError('Нераспознанная строка операции')
            continue
        m = ROW.match(line)
        if not m or not all(MONEY.fullmatch(v) for v in m.group(4, 5, 6)):
            raise StatementError('Нераспознанные колонки операции')
        number, day, clock, outgoing, incoming, balance, tail = m.groups()
        outgoing, incoming, balance = (int(v.replace(',', '')) for v in (outgoing, incoming, balance))
        if (outgoing > 0) == (incoming > 0) or max(outgoing, incoming) > 1_000_000_000:
            raise StatementError('Неоднозначное направление или сумма операции')
        stamp = datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=SEOUL)
        if not start <= stamp.date() <= end:
            raise StatementError('Операция вне периода выписки')
        # In the supplied layout *** introduces bank memo, sometimes wrapped below.
        merchant = (tail or '').split('***', 1)[0].strip()
        if len(merchant) > 200:
            raise StatementError('Слишком длинное описание: возможно, изменился формат PDF')
        rows.append(BankRow(int(number), stamp.astimezone(timezone.utc).isoformat(),
                            incoming - outgoing, balance, merchant, line))
    if not rows or len(rows) > 10000:
        raise StatementError('Нет операций или слишком много строк')
    numbers = [r.number for r in rows]
    if sorted(numbers) != list(range(1, len(rows) + 1)):
        raise StatementError('Неполная выписка или повтор страниц: нарушена нумерация')
    # The supplied PDF is newest first; also accept a reversed bank export if
    # its complete balance chain verifies. Physical PDF row order is irrelevant.
    ordered = sorted(rows, key=lambda r: r.number)
    def valid_chain(sequence):
        return all(newer.occurred_at>=older.occurred_at and newer.balance-newer.amount==older.balance
                   for newer,older in zip(sequence,sequence[1:]))
    if not valid_chain(ordered) and not valid_chain(list(reversed(ordered))):
        raise StatementError('Не сходятся остатки или порядок дат: импорт остановлен')
    account_key = digest(['im', account_number])
    semantic_hash = digest([account_key, sorted((r.identity() for r in rows), key=lambda x: json.dumps(x))])
    return {'bank': 'im', 'account_key': account_key, 'account_label': 'iM Bank · ••••' + account_number[-4:],
            'period_start': str(start), 'period_end': str(end), 'semantic_hash': semantic_hash,
            'parser_version': PARSER_VERSION, 'rows': rows}


def duplicate_status(row, rows, existing):
    """Conservative no-ID matching using balance and a verified neighbour.

Existing entries are dictionaries with id/fingerprint/occurred_at/amount/balance.
A full match with no context is ambiguous. Same timestamp with changed content
is ambiguous. Repeated full identities in either source are never auto-collapsed.
"""
    fp = row.fingerprint()
    exact = [e for e in existing if e['fingerprint'] == fp]
    same_time = [e for e in existing if e['occurred_at'] == row.occurred_at]
    candidates = exact or same_time
    if not candidates:
        return 'new', []
    if len(exact) == 1 and sum(r.fingerprint() == fp for r in rows) == 1:
        # Require another unique matching row connected through the balance chain.
        for other in rows:
            if other.number == row.number or abs(other.number - row.number) != 1:
                continue
            neighbours = [e for e in existing if e['fingerprint'] == other.fingerprint()]
            if len(neighbours) == 1 and sum(r.fingerprint() == other.fingerprint() for r in rows) == 1:
                if row.balance-row.amount==other.balance or other.balance-other.amount==row.balance:
                    return 'repeat', [exact[0]['id']]
    return 'ambiguous', [e['id'] for e in candidates]
