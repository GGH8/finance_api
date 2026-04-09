from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import requests
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, DataTable, Footer, Header, Input, Static

API_BASE_URL = "http://100.107.242.80:8000"
REQUEST_TIMEOUT = 10


def valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def valid_month(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m")
        return True
    except ValueError:
        return False


def normalize_amount(category: str, amount: float) -> float:
    category = category.strip().lower()
    if category == "income":
        return abs(amount)
    return -abs(amount)


def bucket_for_category(category: str) -> str:
    c = category.strip().lower()
    if c == "income":
        return "income"
    if c == "plati bancare":
        return "bank"
    if c == "plati facturi":
        return "bills"
    return "other"


class FinanceApiError(Exception):
    pass


def styled_amount(value: float, privacy: bool = False) -> Text:
    if privacy:
        text = Text("****")
        text.stylize("bold yellow")
        return text

    text = Text(f"{value:+.2f}")
    if value >= 0:
        text.stylize("bold green")
    else:
        text.stylize("bold red")
    return text


def masked_or_number(value: float, privacy: bool, decimals: int = 2) -> str:
    return "****" if privacy else f"{value:.{decimals}f}"


class FinanceApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)

        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            detail = ""
            try:
                if exc.response is not None:
                    payload = exc.response.json()
                    if isinstance(payload, dict):
                        if "detail" in payload:
                            detail = f": {payload['detail']}"
                        elif "message" in payload:
                            detail = f": {payload['message']}"
            except Exception:
                pass
            raise FinanceApiError(f"{method} {path} failed{detail}") from exc

        if response.status_code == 204 or not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise FinanceApiError(f"{method} {path} returned invalid JSON") from exc

    def list_transactions(self, month: str = "", category: str = "") -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if month:
            params["month"] = month
        if category:
            params["category"] = category
        data = self._request("GET", "/transactions", params=params)
        if not isinstance(data, list):
            raise FinanceApiError("GET /transactions returned unexpected data")
        return data

    def get_summary(self, month: str = "", category: str = "") -> dict[str, Any]:
        params: dict[str, str] = {}
        if month:
            params["month"] = month
        if category:
            params["category"] = category
        data = self._request("GET", "/transactions/summary", params=params)
        if not isinstance(data, dict):
            raise FinanceApiError("GET /transactions/summary returned unexpected data")
        return data

    def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/transactions", json=payload)
        if not isinstance(data, dict):
            raise FinanceApiError("POST /transactions returned unexpected data")
        return data

    def update_transaction(self, transaction_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("PUT", f"/transactions/{transaction_id}", json=payload)
        if not isinstance(data, dict):
            raise FinanceApiError("PUT /transactions/{transaction_id} returned unexpected data")
        return data

    def delete_transaction(self, transaction_id: int) -> None:
        self._request("DELETE", f"/transactions/{transaction_id}")

    def list_debts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/debts")
        if not isinstance(data, list):
            raise FinanceApiError("GET /debts returned unexpected data")
        return data

    def create_debt(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/debts", json=payload)
        if not isinstance(data, dict):
            raise FinanceApiError("POST /debts returned unexpected data")
        return data

    def create_debt_payment(self, debt_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", f"/debts/{debt_id}/payments", json=payload)
        if not isinstance(data, dict):
            raise FinanceApiError("POST /debts/{debt_id}/payments returned unexpected data")
        return data


class ConfirmDeleteScreen(ModalScreen[bool]):
    CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }

    #dialog {
        width: 72;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #buttons {
        height: auto;
        align: center middle;
        padding-top: 1;
    }

    Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Confirm delete")
            yield Static(self.label)
            yield Static("Are you sure you want to delete this transaction?")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Delete", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class SummaryBox(Static):
    def update_summary(
        self,
        summary_data: dict[str, Any] | None,
        month_filter: str,
        category_filter: str,
        privacy_mode: bool,
    ) -> None:
        if summary_data is None:
            self.update("Summary\n\nNo data")
            return

        current_balance = float(summary_data.get("current_income", 0))
        total_income = float(summary_data.get("total_income", 0))
        total_expenses = float(summary_data.get("total_expenses", 0))

        active_month = month_filter if month_filter else "all"
        active_category = category_filter if category_filter else "all"

        self.update(
            "\n".join(
                [
                    "Summary",
                    "",
                    f"Month:            {active_month}",
                    f"Category:         {active_category}",
                    "",
                    f"Buget actual:     {masked_or_number(current_balance, privacy_mode)} RON",
                    f"Venituri totale:  {masked_or_number(total_income, privacy_mode)} RON",
                    f"Cheltuieli:       {masked_or_number(total_expenses, privacy_mode)} RON",
                ]
            )
        )


class FinanceApp(App):
    CSS = """
    Screen {
        layout: vertical;
        padding: 0;
        margin: 0;
    }

    Header, Footer {
        padding: 0;
        margin: 0;
    }

    #topbar {
        height: auto;
        padding: 0 1;
        border-bottom: solid $accent;
    }

    #topbar-title {
        height: 1;
        margin: 0 0 1 0;
    }

    #filter-row {
        height: auto;
        margin: 0 0 1 0;
    }

    #filter-month {
        width: 18;
        height: 3;
        margin-right: 1;
    }

    #filter-category {
        width: 18;
        height: 3;
        margin-right: 1;
    }

    #apply-filters, #clear-filters, #toggle-privacy {
        width: 10;
        height: 3;
        margin-right: 1;
    }

    #toggle-privacy {
        margin-right: 0;
    }

    #main {
        height: 1fr;
        margin: 0;
        padding: 0;
    }

    #left {
        width: 4fr;
        padding: 0 1 0 0;
        margin: 0;
        overflow-y: auto;
    }

    #right {
        width: 38;
        min-width: 38;
        max-width: 38;
        padding: 1 1 0 0;
        margin: 0;
        overflow-y: auto;
    }

    Collapsible {
        margin: 0 0 1 0;
        padding: 0;
        border: round $accent-darken-1;
        background: $surface;
    }

    Collapsible > Contents {
        padding: 0;
        margin: 0;
    }

    .group-table {
        height: auto;
        min-height: 4;
        margin: 0;
        padding: 0;
        border: none;
    }

    DataTable {
        margin: 0;
        padding: 0;
        border: none;
        height: auto;
    }

    #group-income, #group-bank, #group-bills, #group-other, #group-debts {
        background: $panel;
    }

    .panel-box {
        height: auto;
        border: round $accent;
        padding: 1;
        margin-bottom: 1;
    }

    .panel-title {
        margin: 0 0 1 0;
        text-style: bold;
    }

    #tx-date, #tx-amount, #tx-category, #tx-description,
    #debt-name, #debt-original-amount,
    #payment-debt-id, #payment-date, #payment-principal, #payment-interest, #payment-description {
        height: 3;
        margin: 0 0 1 0;
    }

    .button-row {
        height: auto;
        margin: 0 0 1 0;
    }

    .button-row Button {
        width: 1fr;
        height: 3;
        margin-right: 1;
    }

    .button-row Button:last-child {
        margin-right: 0;
    }

    .hint {
        margin: 0;
        color: $text-muted;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("a", "focus_transaction_form", "Tx form"),
        Binding("w", "focus_debt_form", "Debt form"),
        Binding("p", "focus_payment_form", "Pay debt"),
        Binding("e,enter", "edit_selected", "Edit"),
        Binding("d", "delete_selected", "Delete"),
        Binding("j", "next_section", "Next section"),
        Binding("k", "prev_section", "Prev section"),
        Binding("escape", "clear_forms", "Clear forms"),
        Binding("/", "focus_month_filter", "Month filter"),
        Binding("f", "focus_category_filter", "Category filter"),
        Binding("v", "toggle_privacy", "Privacy"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.api = FinanceApiClient(API_BASE_URL)
        self.editing_transaction_id: Optional[int] = None
        self.table_order = [
            "table-income",
            "table-bank",
            "table-bills",
            "table-other",
            "table-debts",
        ]
        self.last_active_table_id = "table-income"
        self.last_selected_transaction_id: Optional[int] = None
        self.cached_rows: list[dict[str, Any]] = []
        self.cached_debts: list[dict[str, Any]] = []
        self.privacy_mode = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="topbar"):
            yield Static("Filters", id="topbar-title")
            with Horizontal(id="filter-row"):
                yield Input(placeholder="Month YYYY-MM", id="filter-month")
                yield Input(placeholder="Category", id="filter-category")
                yield Button("Apply", id="apply-filters")
                yield Button("Clear", id="clear-filters")
                yield Button("Privacy", id="toggle-privacy")

        with Horizontal(id="main"):
            with Vertical(id="left"):
                with Collapsible(title="Income", collapsed=False, id="group-income"):
                    yield DataTable(id="table-income", classes="group-table")

                with Collapsible(title="Plăți bancare", collapsed=False, id="group-bank"):
                    yield DataTable(id="table-bank", classes="group-table")

                with Collapsible(title="Plăți facturi", collapsed=False, id="group-bills"):
                    yield DataTable(id="table-bills", classes="group-table")

                with Collapsible(title="Alte cheltuieli", collapsed=False, id="group-other"):
                    yield DataTable(id="table-other", classes="group-table")

                with Collapsible(title="Datorii", collapsed=False, id="group-debts"):
                    yield DataTable(id="table-debts", classes="group-table")

            with Vertical(id="right"):
                yield SummaryBox(id="summary", classes="panel-box")

                with Vertical(classes="panel-box"):
                    yield Static("Add transaction", classes="panel-title")
                    yield Input(placeholder="Date YYYY-MM-DD", id="tx-date")
                    yield Input(placeholder="Amount", id="tx-amount")
                    yield Input(placeholder="Category", id="tx-category")
                    yield Input(placeholder="Description", id="tx-description")
                    with Horizontal(classes="button-row"):
                        yield Button("Save", id="save-transaction")
                        yield Button("Clear", id="clear-transaction")
                    yield Static("a tx form | Enter saves last field", classes="hint")

                with Vertical(classes="panel-box"):
                    yield Static("Add debt", classes="panel-title")
                    yield Input(placeholder="Debt name", id="debt-name")
                    yield Input(placeholder="Original amount", id="debt-original-amount")
                    with Horizontal(classes="button-row"):
                        yield Button("Create debt", id="save-debt")
                    yield Static("w debt form", classes="hint")

                with Vertical(classes="panel-box"):
                    yield Static("Debt payment", classes="panel-title")
                    yield Input(placeholder="Debt ID", id="payment-debt-id")
                    yield Input(placeholder="Date YYYY-MM-DD", id="payment-date")
                    yield Input(placeholder="Principal paid", id="payment-principal")
                    yield Input(placeholder="Interest paid", id="payment-interest")
                    yield Input(placeholder="Description", id="payment-description")
                    with Horizontal(classes="button-row"):
                        yield Button("Save payment", id="save-debt-payment")
                    yield Static("p payment form | v privacy", classes="hint")

        yield Footer()

    def on_mount(self) -> None:
        self.title = "Finance Tracker"
        self.sub_title = "API Client"

        for table_id in ["table-income", "table-bank", "table-bills", "table-other"]:
            table = self.query_one(f"#{table_id}", DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            table.add_columns("ID", "Date", "Amount", "Description")

        debts_table = self.query_one("#table-debts", DataTable)
        debts_table.cursor_type = "row"
        debts_table.zebra_stripes = True
        debts_table.add_columns("ID", "Credit", "Sold rămas", "Principal", "Dobândă", "Ultima plată")

        self.clear_transaction_form()
        self.clear_debt_form()
        self.clear_payment_form()
        self.refresh_all()
        self.focus_table("table-income")

    def get_filters(self) -> tuple[str, str]:
        return (
            self.query_one("#filter-month", Input).value.strip(),
            self.query_one("#filter-category", Input).value.strip(),
        )

    def focus_table(self, table_id: str) -> None:
        table = self.query_one(f"#{table_id}", DataTable)
        self.last_active_table_id = table_id
        table.focus()

    def on_focus(self, event) -> None:
        if isinstance(event.control, DataTable):
            self.last_active_table_id = event.control.id or self.last_active_table_id

    def action_focus_month_filter(self) -> None:
        self.query_one("#filter-month", Input).focus()

    def action_focus_category_filter(self) -> None:
        self.query_one("#filter-category", Input).focus()

    def action_focus_transaction_form(self) -> None:
        self.query_one("#tx-amount", Input).focus()

    def action_focus_debt_form(self) -> None:
        self.query_one("#debt-name", Input).focus()

    def action_focus_payment_form(self) -> None:
        self.query_one("#payment-debt-id", Input).focus()

    def action_toggle_privacy(self) -> None:
        self.privacy_mode = not self.privacy_mode
        self.refresh_all()
        self.notify("Privacy enabled" if self.privacy_mode else "Privacy disabled")

    def fetch_rows(self) -> list[dict[str, Any]]:
        month_filter, category_filter = self.get_filters()

        if month_filter and not valid_month(month_filter):
            self.notify("Month filter invalid. Use YYYY-MM", severity="error")
            return []

        try:
            rows = self.api.list_transactions(month_filter, category_filter)
            self.cached_rows = rows
            return rows
        except FinanceApiError as exc:
            self.notify(str(exc), severity="error")
            self.cached_rows = []
            return []

    def fetch_summary(self) -> dict[str, Any] | None:
        month_filter, category_filter = self.get_filters()
        try:
            return self.api.get_summary(month_filter, category_filter)
        except FinanceApiError as exc:
            self.notify(str(exc), severity="error")
            return None

    def fetch_debts(self) -> list[dict[str, Any]]:
        try:
            debts = self.api.list_debts()
            self.cached_debts = debts
            return debts
        except FinanceApiError as exc:
            self.notify(str(exc), severity="error")
            self.cached_debts = []
            return []

    def set_group_title(self, group_id: str, base_title: str, rows: list[dict[str, Any]]) -> None:
        total = sum(float(row["amount"]) for row in rows)
        collapsible = self.query_one(f"#{group_id}", Collapsible)
        total_label = "****" if self.privacy_mode else f"{total:.2f}"
        collapsible.title = f"{base_title}   {len(rows)}   {total_label} RON"

    def set_debt_group_title(self, rows: list[dict[str, Any]]) -> None:
        total_remaining = sum(float(row["remaining_principal"]) for row in rows)
        collapsible = self.query_one("#group-debts", Collapsible)
        total_label = "****" if self.privacy_mode else f"{total_remaining:.2f}"
        collapsible.title = f"Datorii   {len(rows)}   {total_label} RON"

    def load_transaction_tables(self) -> None:
        grouped = {
            "income": [],
            "bank": [],
            "bills": [],
            "other": [],
        }

        for row in self.fetch_rows():
            grouped[bucket_for_category(str(row["category"]))].append(row)

        mapping = {
            "table-income": grouped["income"],
            "table-bank": grouped["bank"],
            "table-bills": grouped["bills"],
            "table-other": grouped["other"],
        }

        for table_id, rows in mapping.items():
            table = self.query_one(f"#{table_id}", DataTable)
            table.clear(columns=True)
            table.add_columns("ID", "Date", "Amount", "Description")

            if not rows:
                table.add_row("", "", "", "No transactions")
                continue

            for row in rows:
                amount = float(row["amount"])
                table.add_row(
                    str(row["id"]),
                    str(row["date"]),
                    styled_amount(amount, self.privacy_mode),
                    str(row["description"]),
                )

        self.set_group_title("group-income", "Income", grouped["income"])
        self.set_group_title("group-bank", "Plăți bancare", grouped["bank"])
        self.set_group_title("group-bills", "Plăți facturi", grouped["bills"])
        self.set_group_title("group-other", "Alte cheltuieli", grouped["other"])

    def load_debts_table(self) -> None:
        rows = self.fetch_debts()
        table = self.query_one("#table-debts", DataTable)
        table.clear(columns=True)
        table.add_columns("ID", "Credit", "Sold rămas", "Principal", "Dobândă", "Ultima plată")

        if not rows:
            table.add_row("", "No debts", "", "", "", "")
            self.set_debt_group_title([])
            return

        for row in rows:
            remaining = float(row["remaining_principal"])
            total_principal = float(row["total_principal_paid"])
            total_interest = float(row["total_interest_paid"])
            last_payment = row["last_payment_date"] or "-"

            table.add_row(
                str(row["id"]),
                str(row["name"]),
                "****" if self.privacy_mode else f"{remaining:.2f}",
                "****" if self.privacy_mode else f"{total_principal:.2f}",
                "****" if self.privacy_mode else f"{total_interest:.2f}",
                str(last_payment),
            )

        self.set_debt_group_title(rows)

    def restore_selection(self) -> None:
        if self.last_selected_transaction_id is None:
            return

        for table_id in ["table-income", "table-bank", "table-bills", "table-other"]:
            table = self.query_one(f"#{table_id}", DataTable)
            for row_index in range(table.row_count):
                try:
                    row = table.get_row_at(row_index)
                    cell = str(row[0]).strip()
                    if cell and int(cell) == self.last_selected_transaction_id:
                        table.move_cursor(row=row_index, column=0)
                        self.last_active_table_id = table_id
                        return
                except Exception:
                    continue

    def update_summary(self) -> None:
        month_filter, category_filter = self.get_filters()
        summary_data = self.fetch_summary()
        self.query_one("#summary", SummaryBox).update_summary(
            summary_data,
            month_filter,
            category_filter,
            self.privacy_mode,
        )

    def refresh_all(self) -> None:
        current_table = self.last_active_table_id
        self.load_transaction_tables()
        self.load_debts_table()
        self.update_summary()
        self.focus_table(current_table if current_table in self.table_order else "table-income")
        self.restore_selection()

    def get_active_table(self) -> Optional[DataTable]:
        for table_id in self.table_order:
            table = self.query_one(f"#{table_id}", DataTable)
            if table.has_focus:
                self.last_active_table_id = table_id
                return table
        try:
            return self.query_one(f"#{self.last_active_table_id}", DataTable)
        except Exception:
            return None

    def get_selected_transaction_id(self) -> Optional[int]:
        if self.last_active_table_id == "table-debts":
            return None

        table = self.get_active_table()
        if table is None or table.row_count == 0 or table.cursor_row is None:
            return None

        try:
            row = table.get_row_at(table.cursor_row)
            cell = str(row[0]).strip()
            if not cell:
                return None
            selected_id = int(cell)
            self.last_selected_transaction_id = selected_id
            return selected_id
        except Exception:
            return None

    def get_selected_transaction_label(self) -> str:
        transaction_id = self.get_selected_transaction_id()
        if transaction_id is None:
            return "No transaction selected"

        selected = next((row for row in self.cached_rows if int(row["id"]) == transaction_id), None)
        if selected is None:
            return "No transaction selected"

        return (
            f"#{selected['id']} | {selected['date']} | "
            f"{float(selected['amount']):.2f} RON | {selected['description']}"
        )

    def clear_transaction_form(self) -> None:
        self.editing_transaction_id = None
        self.query_one("#tx-date", Input).value = datetime.now().strftime("%Y-%m-%d")
        self.query_one("#tx-amount", Input).value = ""
        self.query_one("#tx-category", Input).value = ""
        self.query_one("#tx-description", Input).value = ""

    def clear_debt_form(self) -> None:
        self.query_one("#debt-name", Input).value = ""
        self.query_one("#debt-original-amount", Input).value = ""

    def clear_payment_form(self) -> None:
        self.query_one("#payment-debt-id", Input).value = ""
        self.query_one("#payment-date", Input).value = datetime.now().strftime("%Y-%m-%d")
        self.query_one("#payment-principal", Input).value = ""
        self.query_one("#payment-interest", Input).value = ""
        self.query_one("#payment-description", Input).value = ""

    def action_clear_forms(self) -> None:
        self.clear_transaction_form()
        self.clear_debt_form()
        self.clear_payment_form()
        self.notify("Forms cleared")

    def action_refresh_data(self) -> None:
        self.refresh_all()
        self.notify("Data refreshed")

    def action_next_section(self) -> None:
        current = self.last_active_table_id
        if current not in self.table_order:
            self.focus_table(self.table_order[0])
            return
        idx = self.table_order.index(current)
        self.focus_table(self.table_order[(idx + 1) % len(self.table_order)])

    def action_prev_section(self) -> None:
        current = self.last_active_table_id
        if current not in self.table_order:
            self.focus_table(self.table_order[0])
            return
        idx = self.table_order.index(current)
        self.focus_table(self.table_order[(idx - 1) % len(self.table_order)])

    def action_edit_selected(self) -> None:
        if self.last_active_table_id == "table-debts":
            table = self.query_one("#table-debts", DataTable)
            if table.cursor_row is None:
                self.notify("Select a debt row first", severity="warning")
                return
            try:
                row = table.get_row_at(table.cursor_row)
                debt_id = str(row[0]).strip()
                if debt_id:
                    self.query_one("#payment-debt-id", Input).value = debt_id
                    self.query_one("#payment-date", Input).focus()
                    self.notify(f"Loaded debt #{debt_id} into payment form")
                return
            except Exception:
                self.notify("Could not load debt row", severity="error")
                return

        transaction_id = self.get_selected_transaction_id()
        if transaction_id is None:
            self.notify("Select a row for edit", severity="warning")
            return

        row = next((item for item in self.cached_rows if int(item["id"]) == transaction_id), None)
        if row is None:
            self.notify("Transaction not found", severity="error")
            return

        self.editing_transaction_id = int(row["id"])
        self.query_one("#tx-date", Input).value = str(row["date"])
        self.query_one("#tx-amount", Input).value = str(abs(float(row["amount"])))
        self.query_one("#tx-category", Input).value = str(row["category"])
        self.query_one("#tx-description", Input).value = str(row["description"])
        self.query_one("#tx-amount", Input).focus()
        self.notify(f"Loaded #{row['id']} for edit")

    def action_delete_selected(self) -> None:
        if self.last_active_table_id == "table-debts":
            self.notify("Debt deletion is not implemented in this version", severity="warning")
            return

        transaction_id = self.get_selected_transaction_id()
        if transaction_id is None:
            self.notify("Select a row for delete", severity="warning")
            return

        label = self.get_selected_transaction_label()

        def after_confirm(confirmed: bool) -> None:
            if not confirmed:
                self.notify("Delete cancelled")
                return

            try:
                self.api.delete_transaction(transaction_id)
            except FinanceApiError as exc:
                self.notify(str(exc), severity="error")
                return

            if self.editing_transaction_id == transaction_id:
                self.clear_transaction_form()

            self.last_selected_transaction_id = None
            self.refresh_all()
            self.notify(f"Deleted transaction #{transaction_id}")

        self.push_screen(ConfirmDeleteScreen(label), after_confirm)

    def save_transaction_form(self) -> None:
        date_value = self.query_one("#tx-date", Input).value.strip()
        category = self.query_one("#tx-category", Input).value.strip()
        description = self.query_one("#tx-description", Input).value.strip()

        if not valid_date(date_value):
            self.notify("Date invalid. Use YYYY-MM-DD", severity="error")
            return

        try:
            raw_amount = float(self.query_one("#tx-amount", Input).value.replace(",", ".").strip())
        except ValueError:
            self.notify("Amount invalid", severity="error")
            return

        if not category:
            self.notify("Category is required", severity="error")
            return

        if not description:
            self.notify("Description is required", severity="error")
            return

        payload = {
            "date": date_value,
            "amount": normalize_amount(category, raw_amount),
            "category": category,
            "description": description,
        }

        try:
            if self.editing_transaction_id is None:
                created = self.api.create_transaction(payload)
                self.last_selected_transaction_id = int(created["id"])
                self.notify(f"Saved transaction #{created['id']}")
            else:
                edited_id = self.editing_transaction_id
                updated = self.api.update_transaction(edited_id, payload)
                self.last_selected_transaction_id = int(updated["id"])
                self.notify(f"Updated transaction #{edited_id}")
        except FinanceApiError as exc:
            self.notify(str(exc), severity="error")
            return

        self.clear_transaction_form()
        self.refresh_all()

    def save_debt_form(self) -> None:
        name = self.query_one("#debt-name", Input).value.strip()
        created_at = datetime.now().strftime("%Y-%m-%d")

        if not name:
            self.notify("Debt name is required", severity="error")
            return

        try:
            original_amount = float(self.query_one("#debt-original-amount", Input).value.replace(",", ".").strip())
        except ValueError:
            self.notify("Original amount invalid", severity="error")
            return

        payload = {
            "name": name,
            "original_amount": original_amount,
            "created_at": created_at,
        }

        try:
            debt = self.api.create_debt(payload)
            self.notify(f"Created debt #{debt['id']}")
        except FinanceApiError as exc:
            self.notify(str(exc), severity="error")
            return

        self.clear_debt_form()
        self.refresh_all()

    def save_payment_form(self) -> None:
        debt_id_value = self.query_one("#payment-debt-id", Input).value.strip()
        date_value = self.query_one("#payment-date", Input).value.strip()
        description = self.query_one("#payment-description", Input).value.strip()

        if not debt_id_value:
            self.notify("Debt ID is required", severity="error")
            return

        try:
            debt_id = int(debt_id_value)
        except ValueError:
            self.notify("Debt ID must be an integer", severity="error")
            return

        if not valid_date(date_value):
            self.notify("Date invalid. Use YYYY-MM-DD", severity="error")
            return

        try:
            principal_paid = float(self.query_one("#payment-principal", Input).value.replace(",", ".").strip() or "0")
            interest_paid = float(self.query_one("#payment-interest", Input).value.replace(",", ".").strip() or "0")
        except ValueError:
            self.notify("Principal/interest values are invalid", severity="error")
            return

        if not description:
            self.notify("Description is required", severity="error")
            return

        payload = {
            "date": date_value,
            "principal_paid": principal_paid,
            "interest_paid": interest_paid,
            "description": description,
            "create_transaction": True,
        }

        try:
            self.api.create_debt_payment(debt_id, payload)
            self.notify(f"Saved payment for debt #{debt_id}")
        except FinanceApiError as exc:
            self.notify(str(exc), severity="error")
            return

        self.clear_payment_form()
        self.refresh_all()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id

        if button_id == "apply-filters":
            month_filter = self.query_one("#filter-month", Input).value.strip()
            if month_filter and not valid_month(month_filter):
                self.notify("Month filter invalid. Use YYYY-MM", severity="error")
                return
            self.refresh_all()
            self.notify("Filters applied")
        elif button_id == "clear-filters":
            self.query_one("#filter-month", Input).value = ""
            self.query_one("#filter-category", Input).value = ""
            self.refresh_all()
            self.notify("Filters cleared")
        elif button_id == "toggle-privacy":
            self.action_toggle_privacy()
        elif button_id == "save-transaction":
            self.save_transaction_form()
        elif button_id == "clear-transaction":
            self.clear_transaction_form()
        elif button_id == "save-debt":
            self.save_debt_form()
        elif button_id == "save-debt-payment":
            self.save_payment_form()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id

        if input_id == "tx-date":
            self.query_one("#tx-amount", Input).focus()
        elif input_id == "tx-amount":
            self.query_one("#tx-category", Input).focus()
        elif input_id == "tx-category":
            self.query_one("#tx-description", Input).focus()
        elif input_id == "tx-description":
            self.save_transaction_form()
        elif input_id == "debt-name":
            self.query_one("#debt-original-amount", Input).focus()
        elif input_id == "debt-original-amount":
            self.save_debt_form()
        elif input_id == "payment-debt-id":
            self.query_one("#payment-date", Input).focus()
        elif input_id == "payment-date":
            self.query_one("#payment-principal", Input).focus()
        elif input_id == "payment-principal":
            self.query_one("#payment-interest", Input).focus()
        elif input_id == "payment-interest":
            self.query_one("#payment-description", Input).focus()
        elif input_id == "payment-description":
            self.save_payment_form()
        elif input_id in {"filter-month", "filter-category"}:
            month_filter = self.query_one("#filter-month", Input).value.strip()
            if month_filter and not valid_month(month_filter):
                self.notify("Month filter invalid. Use YYYY-MM", severity="error")
                return
            self.refresh_all()
            self.notify("Filters applied")


if __name__ == "__main__":
    app = FinanceApp()
    app.run()
