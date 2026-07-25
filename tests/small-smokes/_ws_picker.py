"""Drive the sidebar's workspace picker from a browser smoke.

The picker used to be a native `<select class="ws-select">`, so smokes read
`.input_value()` and called `select_option(value=<id>)`. It is now the same
type-to-filter combobox as the model picker (`PickerDropdown` + `Typeahead`),
which has no value and no <option>s. Two hooks replace them:

  - `.ws-picker[data-ws-id]` — the wrapper mirrors `ws.activeId` (the oracle)
  - `.typeahead-row[data-id=<id>]` — one row per workspace, addressable by id

Rows carry the workspace NAME; the id is matched by the filter (items pass it
as hidden `search` text), which is how `pick_workspace` narrows a capped list.
"""

WS_TRIGGER = ".ws-picker .picker-dropdown-trigger"
WS_INPUT = ".ws-picker .typeahead-input"


def active_ws_id(page) -> str:
    """The open workspace's id — the old `select.ws-select`.value."""
    return page.get_attribute(".ws-picker", "data-ws-id") or ""


def wait_active_ws(page, want: str, timeout: int = 10000) -> None:
    page.wait_for_function(
        "([want]) => document.querySelector('.ws-picker')?.dataset.wsId === want",
        arg=[want], timeout=timeout)


def pick_workspace(page, ws_id: str, timeout: int = 8000) -> None:
    """Open the combobox and pick `ws_id` — the old `select_option(value=…)`."""
    row = f".ws-picker .typeahead-row[data-id='{ws_id}']"
    page.wait_for_selector(WS_TRIGGER, timeout=timeout)
    page.locator(WS_TRIGGER).click()
    page.wait_for_selector(WS_INPUT, timeout=timeout)
    if not page.locator(row).count():  # beyond the row cap → filter by id
        page.locator(WS_INPUT).fill(ws_id)
    page.wait_for_selector(row, timeout=timeout)
    page.locator(row).click()
