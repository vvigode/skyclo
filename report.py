"""Отчёт: аванс по договору.

Запуск из корня репозитория:

    python report.py
    python report.py --date 2026-11-10
    python report.py --check

Нужен Python 3.11 или новее. Сторонние библиотеки не нужны.
Результат: out/works.csv и out/contracts.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

KOPECK = Decimal("0.01")
ZERO = Decimal("0.00")
ROOT = Path(__file__).resolve().parent

STATUS_LABEL = {
    "done": "выполнена",
    "planned": "запланирована",
    "cancelled": "отменена",
}


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: str
    purpose: str
    amount: Decimal
    status: str
    created_at: date | None
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True)
class Cash:
    paid: Decimal
    queued: Decimal
    after_date: Decimal
    returned: Decimal
    reason: str | None


@dataclass(frozen=True)
class WorkRow:
    contract_id: str
    contractor: str
    work_id: str
    title: str
    status: str
    completed_at: date | None
    price: Decimal
    direct: Decimal
    advance: Decimal
    paid: Decimal
    left: Decimal
    debt: Decimal


@dataclass(frozen=True)
class ContractRow:
    contract_id: str
    contractor: str
    amount: Decimal
    all_prices: Decimal
    active_prices: Decimal
    paid: Decimal
    left: Decimal
    debt: Decimal
    unallocated: Decimal
    queued: Decimal
    after_date: Decimal


@dataclass(frozen=True)
class SkipRow:
    payment_id: str
    contract_id: str
    work_id: str
    amount: Decimal
    reason: str


@dataclass(frozen=True)
class Report:
    as_of: date
    works: tuple[WorkRow, ...]
    contracts: tuple[ContractRow, ...]
    skipped: tuple[SkipRow, ...]
    warnings: tuple[str, ...]


def parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def parse_money(value: str) -> Decimal:
    return Decimal(value.strip()).quantize(KOPECK)


def show(value: Decimal) -> str:
    return f"{value.quantize(KOPECK):f}"


def load_data(folder: Path) -> tuple[tuple[Contract, ...], tuple[Work, ...], tuple[Payment, ...]]:
    contracts = tuple(
        Contract(
            row["contract_id"].strip(),
            row["contractor"].strip(),
            parse_money(row["amount"]),
            parse_date(row["signed_at"]) or date.min,
        )
        for row in read_csv(folder / "contracts.csv")
    )
    works = tuple(
        Work(
            row["work_id"].strip(),
            row["contract_id"].strip(),
            row["title"].strip(),
            parse_money(row["price"]),
            parse_date(row["completed_at"]),
            parse_date(row["cancelled_at"]),
        )
        for row in read_csv(folder / "works.csv")
    )
    payments = tuple(
        Payment(
            row["payment_id"].strip(),
            row["contract_id"].strip(),
            row["work_id"].strip(),
            row["purpose"].strip(),
            parse_money(row["amount"]),
            row["status"].strip(),
            parse_date(row["created_at"]),
            parse_date(row["paid_at"]),
            parse_date(row["refunded_at"]),
        )
        for row in read_csv(folder / "payments.csv")
    )
    return contracts, works, payments


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def work_status(work: Work, as_of: date) -> str:
    if work.cancelled_at is not None and work.cancelled_at <= as_of:
        return "cancelled"
    if work.completed_at is not None and work.completed_at <= as_of:
        return "done"
    return "planned"


def classify(payment: Payment, as_of: date) -> Cash:
    amount = payment.amount
    if payment.status == "queued":
        if payment.created_at is not None and payment.created_at <= as_of:
            return Cash(ZERO, amount, ZERO, ZERO, "в очереди, деньги не ушли")
        return Cash(ZERO, ZERO, ZERO, ZERO, "очередь заведена позже отчёта")
    if payment.paid_at is None or payment.paid_at > as_of:
        return Cash(ZERO, ZERO, amount, ZERO, "дата оплаты позже отчёта")
    if payment.status == "refunded":
        if payment.refunded_at is not None and payment.refunded_at <= as_of:
            return Cash(ZERO, ZERO, ZERO, amount, "возврат до даты отчёта")
        return Cash(amount, ZERO, ZERO, ZERO, None)
    if payment.status == "paid":
        return Cash(amount, ZERO, ZERO, ZERO, None)
    return Cash(ZERO, ZERO, ZERO, ZERO, "неизвестный статус")


def work_order(work_id: str) -> tuple[str, int]:
    prefix, separator, tail = work_id.partition("-")
    if separator and tail.isdigit():
        return prefix, int(tail)
    return work_id, 0


def build_report(
    contracts: tuple[Contract, ...],
    works: tuple[Work, ...],
    payments: tuple[Payment, ...],
    as_of: date,
) -> Report:
    visible_contracts = tuple(item for item in contracts if item.signed_at <= as_of)
    contract_ids = {item.contract_id for item in visible_contracts}
    contractors = {item.contract_id: item.contractor for item in visible_contracts}
    visible_works = tuple(item for item in works if item.contract_id in contract_ids)
    works_by_id = {item.work_id: item for item in visible_works}
    warnings = link_warnings(payments, works_by_id, contract_ids)
    grouped = group_payments(payments, works_by_id, as_of)
    work_rows = tuple(
        row
        for contract in sorted(visible_contracts, key=lambda item: item.contract_id)
        for row in rows_for_contract(
            contract,
            tuple(item for item in visible_works if item.contract_id == contract.contract_id),
            grouped,
            as_of,
        )
    )
    contract_rows = tuple(
        contract_row(contract, work_rows, grouped) for contract in sorted(visible_contracts, key=lambda item: item.contract_id)
    )
    skipped = tuple(grouped["skipped"])
    return Report(as_of, work_rows, contract_rows, skipped, warnings)


def link_warnings(
    payments: tuple[Payment, ...],
    works_by_id: dict[str, Work],
    contract_ids: set[str],
) -> tuple[str, ...]:
    notes: list[str] = []
    for payment in payments:
        if payment.contract_id not in contract_ids:
            continue
        if not payment.work_id:
            continue
        work = works_by_id.get(payment.work_id)
        if work is None:
            notes.append(f"{payment.payment_id}: работы {payment.work_id} нет, платёж оставлен на договоре")
        elif work.contract_id != payment.contract_id:
            notes.append(f"{payment.payment_id}: работа {payment.work_id} из другого договора, платёж оставлен на договоре")
    return tuple(notes)


def group_payments(
    payments: tuple[Payment, ...],
    works_by_id: dict[str, Work],
    as_of: date,
) -> dict[str, object]:
    direct: dict[str, Decimal] = {}
    pool: dict[str, Decimal] = {}
    queued: dict[str, Decimal] = {}
    after: dict[str, Decimal] = {}
    skipped: list[SkipRow] = []
    for payment in payments:
        cash = classify(payment, as_of)
        if cash.reason:
            skipped.append(
                SkipRow(payment.payment_id, payment.contract_id, payment.work_id, payment.amount, cash.reason)
            )
        queued[payment.contract_id] = queued.get(payment.contract_id, ZERO) + cash.queued
        after[payment.contract_id] = after.get(payment.contract_id, ZERO) + cash.after_date
        if cash.paid == ZERO:
            continue
        work = works_by_id.get(payment.work_id)
        if payment.work_id and work is not None and work.contract_id == payment.contract_id:
            direct[payment.work_id] = direct.get(payment.work_id, ZERO) + cash.paid
        else:
            pool[payment.contract_id] = pool.get(payment.contract_id, ZERO) + cash.paid
    return {"direct": direct, "pool": pool, "queued": queued, "after": after, "skipped": skipped}


def rows_for_contract(
    contract: Contract,
    works: tuple[Work, ...],
    grouped: dict[str, object],
    as_of: date,
) -> tuple[WorkRow, ...]:
    direct_map = grouped["direct"]
    base = tuple(
        draft_row(contract, work, as_of, direct_map.get(work.work_id, ZERO))
        for work in sorted(works, key=lambda item: work_order(item.work_id))
    )
    pool = grouped["pool"].get(contract.contract_id, ZERO)
    assigned, _unallocated = assign_pool(base, pool)
    return tuple(finish_row(row, assigned.get(row.work_id, ZERO)) for row in base)


def draft_row(contract: Contract, work: Work, as_of: date, direct: Decimal) -> WorkRow:
    status = work_status(work, as_of)
    return WorkRow(
        contract.contract_id,
        contract.contractor,
        work.work_id,
        work.title,
        status,
        work.completed_at,
        work.price,
        direct,
        ZERO,
        direct,
        ZERO,
        ZERO,
    )


def assign_pool(rows: tuple[WorkRow, ...], pool: Decimal) -> tuple[dict[str, Decimal], Decimal]:
    done = sorted(
        (row for row in rows if row.status == "done"),
        key=lambda row: (row.completed_at or date.max, work_order(row.work_id)),
    )
    planned = sorted(
        (row for row in rows if row.status == "planned"),
        key=lambda row: work_order(row.work_id),
    )
    remaining = pool
    assigned: dict[str, Decimal] = {}
    for row in (*done, *planned):
        room = row.price - row.direct
        if room < ZERO:
            room = ZERO
        take = room if room < remaining else remaining
        assigned[row.work_id] = take
        remaining -= take
    return assigned, remaining


def finish_row(row: WorkRow, advance: Decimal) -> WorkRow:
    paid = (row.direct + advance).quantize(KOPECK)
    if row.status == "cancelled":
        left = (ZERO - row.direct).quantize(KOPECK)
    else:
        left = (row.price - paid).quantize(KOPECK)
    debt = left if row.status == "done" and left > ZERO else ZERO
    return WorkRow(
        row.contract_id,
        row.contractor,
        row.work_id,
        row.title,
        row.status,
        row.completed_at,
        row.price,
        row.direct,
        advance.quantize(KOPECK),
        paid,
        left,
        debt,
    )


def contract_row(contract: Contract, works: tuple[WorkRow, ...], grouped: dict[str, object]) -> ContractRow:
    own = tuple(row for row in works if row.contract_id == contract.contract_id)
    unallocated = grouped["pool"].get(contract.contract_id, ZERO) - sum((row.advance for row in own), ZERO)
    paid = (sum((row.paid for row in own), ZERO) + unallocated).quantize(KOPECK)
    return ContractRow(
        contract.contract_id,
        contract.contractor,
        contract.amount,
        sum((row.price for row in own), ZERO).quantize(KOPECK),
        sum((row.price for row in own if row.status != "cancelled"), ZERO).quantize(KOPECK),
        paid,
        sum((row.left for row in own), ZERO).quantize(KOPECK),
        sum((row.debt for row in own), ZERO).quantize(KOPECK),
        unallocated.quantize(KOPECK),
        grouped["queued"].get(contract.contract_id, ZERO).quantize(KOPECK),
        grouped["after"].get(contract.contract_id, ZERO).quantize(KOPECK),
    )


def write_report(report: Report, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    write_table(
        folder / "works.csv",
        (
            "договор",
            "подрядчик",
            "работа",
            "название",
            "статус_на_дату",
            "цена",
            "оплачено_прямо",
            "оплачено_из_аванса",
            "выплачено",
            "осталось",
            "долг",
        ),
        (
            (
                row.contract_id,
                row.contractor,
                row.work_id,
                row.title,
                STATUS_LABEL[row.status],
                show(row.price),
                show(row.direct),
                show(row.advance),
                show(row.paid),
                show(row.left),
                show(row.debt),
            )
            for row in report.works
        ),
    )
    write_table(
        folder / "contracts.csv",
        (
            "договор",
            "подрядчик",
            "сумма_договора",
            "цена_всех_работ",
            "цена_без_отменённых",
            "выплачено",
            "осталось",
            "долг_по_принятым",
            "аванс_сверх_работ",
            "в_очереди",
            "оплачено_после_даты",
        ),
        (
            (
                row.contract_id,
                row.contractor,
                show(row.amount),
                show(row.all_prices),
                show(row.active_prices),
                show(row.paid),
                show(row.left),
                show(row.debt),
                show(row.unallocated),
                show(row.queued),
                show(row.after_date),
            )
            for row in report.contracts
        ),
    )
    write_table(
        folder / "skipped.csv",
        ("платёж", "договор", "работа", "сумма", "почему_не_вошло"),
        (
            (row.payment_id, row.contract_id, row.work_id, show(row.amount), row.reason)
            for row in report.skipped
        ),
    )


def write_table(path: Path, header: tuple[str, ...], rows: object) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)


def print_report(report: Report) -> None:
    print(f"Дата отчёта: {report.as_of.strftime('%d.%m.%Y')}")
    for contract in report.contracts:
        print()
        print(f"{contract.contract_id}  {contract.contractor}")
        print(
            "  сумма договора "
            f"{show(contract.amount)}, выплачено {show(contract.paid)}, "
            f"осталось {show(contract.left)}, долг по принятым {show(contract.debt)}"
        )
        print(
            "  в очереди "
            f"{show(contract.queued)}, оплачено после даты {show(contract.after_date)}, "
            f"аванс сверх работ {show(contract.unallocated)}"
        )
        for row in report.works:
            if row.contract_id != contract.contract_id:
                continue
            print(
                f"  {row.work_id}  {STATUS_LABEL[row.status]:<14}  "
                f"цена {show(row.price)}  выплачено {show(row.paid)}  "
                f"осталось {show(row.left)}  долг {show(row.debt)}"
            )
    for note in report.warnings:
        print(f"предупреждение: {note}", file=sys.stderr)


def check_sample(folder: Path) -> None:
    contracts, works, payments = load_data(folder)
    on_report_day = build_report(contracts, works, payments, date(2026, 11, 10))
    expect_work(on_report_day, "W-11", "done", "0.00", "100000.00", "100000.00", "0.00", "0.00")
    expect_work(on_report_day, "W-12", "done", "0.00", "100000.00", "100000.00", "0.00", "0.00")
    expect_work(on_report_day, "W-13", "cancelled", "0.00", "0.00", "0.00", "0.00", "0.00")
    expect_work(on_report_day, "W-14", "done", "15000.00", "85000.00", "100000.00", "0.00", "0.00")
    expect_work(on_report_day, "W-15", "planned", "0.00", "15000.00", "15000.00", "85000.00", "0.00")
    expect_work(on_report_day, "W-16", "planned", "0.00", "0.00", "0.00", "100000.00", "0.00")
    expect_work(on_report_day, "W-21", "done", "0.00", "20000.00", "20000.00", "0.00", "0.00")
    expect_work(on_report_day, "W-22", "done", "0.00", "30000.00", "30000.00", "0.00", "0.00")
    expect_work(on_report_day, "W-23", "planned", "0.00", "25000.00", "25000.00", "15000.00", "0.00")
    expect_contract(on_report_day, "D-1", "315000.00", "185000.00", "0.00", "150000.00", "0.00")
    expect_contract(on_report_day, "D-2", "75000.00", "15000.00", "0.00", "0.00", "20000.00")
    before_refund = build_report(contracts, works, payments, date(2026, 9, 2))
    expect_work(before_refund, "W-13", "planned", "0.00", "100000.00", "100000.00", "0.00", "0.00")
    expect_work(before_refund, "W-14", "planned", "0.00", "50000.00", "50000.00", "50000.00", "0.00")
    expect_contract(before_refund, "D-1", "350000.00", "250000.00", "0.00", "0.00", "15000.00")
    after_refund = build_report(contracts, works, payments, date(2026, 9, 10))
    expect_work(after_refund, "W-14", "planned", "0.00", "0.00", "0.00", "100000.00", "0.00")
    expect_contract(after_refund, "D-1", "300000.00", "300000.00", "0.00", "0.00", "15000.00")
    after_cancel = build_report(contracts, works, payments, date(2026, 10, 20))
    expect_work(after_cancel, "W-13", "cancelled", "0.00", "0.00", "0.00", "0.00", "0.00")
    expect_work(after_cancel, "W-14", "planned", "0.00", "100000.00", "100000.00", "0.00", "0.00")
    expect_work(after_cancel, "W-22", "done", "0.00", "25000.00", "25000.00", "5000.00", "5000.00")
    not_paid_yet = build_report(contracts, works, payments, date(2026, 8, 31))
    expect_contract(not_paid_yet, "D-1", "0.00", "600000.00", "0.00", "0.00", "365000.00")
    for report in (on_report_day, before_refund, after_refund, after_cancel):
        assert_balanced(report)
    check_completion_order()


def check_completion_order() -> None:
    contracts = (Contract("D", "Подрядчик", Decimal("300.00"), date(2026, 1, 1)),)
    works = (
        Work("W-1", "D", "поздняя", Decimal("100.00"), date(2026, 3, 1), None),
        Work("W-2", "D", "ранняя", Decimal("100.00"), date(2026, 2, 1), None),
        Work("W-3", "D", "будущая", Decimal("100.00"), None, None),
    )
    payments = (
        Payment("P", "D", "", "аванс", Decimal("150.00"), "paid", date(2026, 1, 2), date(2026, 1, 2), None),
    )
    report = build_report(contracts, works, payments, date(2026, 4, 1))
    expect_work(report, "W-2", "done", "0.00", "100.00", "100.00", "0.00", "0.00")
    expect_work(report, "W-1", "done", "0.00", "50.00", "50.00", "50.00", "50.00")
    expect_work(report, "W-3", "planned", "0.00", "0.00", "0.00", "100.00", "0.00")
    assert_balanced(report)
    check_overpay()


def check_overpay() -> None:
    contracts = (Contract("D", "Подрядчик", Decimal("100.00"), date(2026, 1, 1)),)
    works = (Work("W-1", "D", "работа", Decimal("100.00"), date(2026, 2, 1), None),)
    payments = (
        Payment("P", "D", "", "аванс", Decimal("150.00"), "paid", date(2026, 1, 2), date(2026, 1, 2), None),
    )
    report = build_report(contracts, works, payments, date(2026, 3, 1))
    expect_work(report, "W-1", "done", "0.00", "100.00", "100.00", "0.00", "0.00")
    expect_contract(report, "D", "150.00", "0.00", "0.00", "0.00", "0.00")
    if show(report.contracts[0].unallocated) != "50.00":
        raise SystemExit("сверхплатёж не остался на договоре")
    assert_balanced(report)


def expect_work(
    report: Report,
    work_id: str,
    status: str,
    direct: str,
    advance: str,
    paid: str,
    left: str,
    debt: str,
) -> None:
    row = next(item for item in report.works if item.work_id == work_id)
    actual = (row.status, show(row.direct), show(row.advance), show(row.paid), show(row.left), show(row.debt))
    expected = (status, direct, advance, paid, left, debt)
    if actual != expected:
        raise SystemExit(f"{work_id} на {report.as_of}: {actual} != {expected}")


def expect_contract(
    report: Report,
    contract_id: str,
    paid: str,
    left: str,
    debt: str,
    queued: str,
    after_date: str,
) -> None:
    row = next(item for item in report.contracts if item.contract_id == contract_id)
    actual = (show(row.paid), show(row.left), show(row.debt), show(row.queued), show(row.after_date))
    expected = (paid, left, debt, queued, after_date)
    if actual != expected:
        raise SystemExit(f"{contract_id} на {report.as_of}: {actual} != {expected}")


def assert_balanced(report: Report) -> None:
    for contract in report.contracts:
        own = tuple(row for row in report.works if row.contract_id == contract.contract_id)
        paid = sum((row.paid for row in own), ZERO) + contract.unallocated
        if paid != contract.paid:
            raise SystemExit(f"{contract.contract_id}: сумма по работам не сходится с договором")
        if contract.unallocated < ZERO:
            raise SystemExit(f"{contract.contract_id}: аванс сверх работ ушёл в минус")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Отчёт по авансу договора на выбранную дату")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--date", default="2026-11-10", help="Дата отчёта, ГГГГ-ММ-ДД")
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--check", action="store_true", help="Проверить расчёт на контрольных датах")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.check:
        check_sample(args.data)
    as_of = parse_date(args.date)
    if as_of is None:
        raise SystemExit("Нужна дата отчёта в виде ГГГГ-ММ-ДД")
    contracts, works, payments = load_data(args.data)
    report = build_report(contracts, works, payments, as_of)
    write_report(report, args.out)
    print_report(report)
    print()
    print(f"Файлы: {args.out / 'works.csv'}")
    print(f"       {args.out / 'contracts.csv'}")
    print(f"       {args.out / 'skipped.csv'}")


if __name__ == "__main__":
    main()
