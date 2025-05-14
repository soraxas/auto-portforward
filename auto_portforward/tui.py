#!/usr/bin/python
import asyncio
import logging
import os
import threading
from typing import Dict, Set
from textual import on, work
from textual.app import App, ComposeResult
from textual.message_pump import Timer
from textual.message import Message
from textual.widgets import Header, Footer, Static, Tree
from textual.binding import Binding
from textual.widgets import Log


# from textual.style import Style
from rich.style import Style
from rich.text import Text

from auto_portforward.process_provider.abstract_provider import AbstractProvider

from .process_provider.ssh_remote import RemoteProcessMonitor
from .datatype import Process

# Configure logging
log_dir = os.path.expanduser("logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "tui.log")

# Create formatters
FORMATTER = logging.Formatter("[%(asctime)s %(levelname)-5s] %(message)s", datefmt="%H:%M:%S")

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Add file handler for main log
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(FORMATTER)
root_logger.addHandler(file_handler)

LOGGER = logging.getLogger(__file__)

GROUP_SELECTED_STYLE = Style(color="green", italic=True)
NODE_SELECTED_STYLE = Style(color="yellow", italic=True)


class ProcessTree(Tree):
    """
    A tree of processes.
    """

    def __init__(self, monitor: AbstractProvider, logger: Log):
        super().__init__(monitor.name)
        self.monitor: AbstractProvider = monitor
        self.last_memory: Dict[str, Process] = {}
        self.selected_groups: Set[str] = set()
        self.selected_processes: Set[int] = set()
        self.group_by = "cwd"
        self.sort_reverse = False
        self.filter_text = ""
        self.update_interval = 1.0
        self.last_update = 0
        self.regular_update_timer: Timer | None = None
        self.logger: Log = logger

    def on_mount(self) -> None:
        def expand_all(node):
            node.expand()
            for child in node.children:
                expand_all(child)

        expand_all(self.root)
        # Start continuous update loop
        # self.regular_update_timer = self.set_interval(
        #     lambda: self.update_processes,
        #     self.update_interval,
        # )

        if not self.call_later(self.update_processes):
            raise RuntimeError("Failed to schedule update_processes")

    def is_new_memory(self, new_memory: Dict[str, Process]) -> bool:
        if not self.last_memory:
            return True
        if len(new_memory) != len(self.last_memory):
            return True
        for pid, process in new_memory.items():
            if process != self.last_memory[pid]:
                return True
        return False

    @work(exclusive=True)
    async def update_processes(self) -> None:
        new_memory = await self.monitor.get_processes()
        if new_memory and self.is_new_memory(new_memory):
            self.last_memory = new_memory.copy()
            await self.update_process_layout()

        await asyncio.sleep(1)
        # Schedule next update immediately
        self.call_later(self.update_processes)

    async def toggle_group(self, group_key: str) -> None:
        LOGGER.debug("Toggling group: %s", group_key)
        # Convert Text object to string if needed
        if hasattr(group_key, "plain"):
            group_key = group_key.plain
        if group_key in self.selected_groups:
            self.selected_groups.remove(group_key)
        else:
            self.selected_groups.add(group_key)
        await self.update_process_layout()

    async def toggle_process(self, pid: int) -> None:
        if pid in self.selected_processes:
            self.selected_processes.remove(pid)
        else:
            self.selected_processes.add(pid)
        await self.update_process_layout()

    async def update_process_layout(self) -> None:
        ports_to_forward = set()

        # Group processes
        grouped: Dict[str, list[Process]] = {}
        for pid, process in self.last_memory.items():
            if self.filter_text and self.filter_text.lower() not in process.name.lower():
                continue
            key = getattr(process, self.group_by)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(process)

        # Sort groups
        sorted_groups = sorted(
            grouped.items(),
            key=lambda x: str(x[0]) if x[0] is not None else "",
            reverse=self.sort_reverse,
        )

        # clear any existing nodes
        self.clear()

        # Create tree structure
        for group, processes in sorted_groups:
            group_key = str(group) if group is not None else "Unknown"
            selected_by_group = group_key in self.selected_groups

            if self.group_by != "pid":
                # Create group node
                group_node = self.root.add(group_key, expand=True)
                group_node.data = {"is_group": True}

                if selected_by_group:
                    group_node.label.style = GROUP_SELECTED_STYLE

                group_or_root_node = group_node
            else:
                # PID does not needs grouping.
                group_or_root_node = self.root

            # Sort processes
            sorted_processes = sorted(
                processes,
                key=lambda p: str(p.pid) if p.pid is not None else "0",
                reverse=self.sort_reverse,
            )

            for process in sorted_processes:
                parts = []
                if process.tcp:
                    parts.extend(
                        [
                            (" 🌐", "bold cyan"),
                            ("TCP", "bold cyan u"),
                            (": ", "bold cyan"),
                            f"{','.join(map(str, process.tcp))}",
                        ]
                    )
                if process.udp:
                    parts.append((" 📡UDP: ", "bold red"))
                    parts.append(f"{','.join(map(str, process.udp))}")

                process_node = group_or_root_node.add_leaf(
                    Text.assemble(
                        ("🆔", ""),
                        (f"{process.pid}", "bold"),
                        (" 📦", ""),
                        (f"{process.name}", "blue"),
                        *parts,
                        (f" (⚡{process.status})", ""),
                        overflow="ellipsis",
                        justify="center",
                    )
                )
                # Add process node
                process_node.data = {"is_group": False, "pid": process.pid}

                # selected can also be done on a node-level
                if selected_by_group:
                    process_node.label.style = GROUP_SELECTED_STYLE
                elif process.pid in self.selected_processes:
                    process_node.label.style = NODE_SELECTED_STYLE
                else:
                    continue

                # Add ports to forward
                for p in process.tcp:
                    ports_to_forward.add(p)

        self.call_later(self.update_toggled_ports, ports_to_forward)

    @work(exclusive=True)
    async def update_toggled_ports(self, ports_to_forward: Set[int]) -> None:
        await self.monitor.set_toggled_ports(ports_to_forward)

    async def on_unmount(self) -> None:
        """Clean up all port forwarding processes when the widget is removed."""

        if self.regular_update_timer:
            self.regular_update_timer.stop()

        await self.monitor.cleanup()

    async def change_group_by(self) -> None:
        options = ["cwd", "name", "pid"]
        current_index = options.index(self.group_by)
        self.group_by = options[(current_index + 1) % len(options)]
        await self.update_process_layout()

    async def toggle_sort(self) -> None:
        self.sort_reverse = not self.sort_reverse
        await self.update_process_layout()

    async def set_filter(self, text: str) -> None:
        self.filter_text = text
        await self.update_process_layout()


class TuiLogHandler(logging.Handler):
    class NewLog(Message):
        """
        This is a message that is sent to the TUI logger.
        """

        def __init__(self, msg: str):
            super().__init__()
            self.msg = msg

    def __init__(self, tui_logger: Log):
        super().__init__()
        self.tui_logger = tui_logger
        self._lock = threading.Lock()

    def emit(self, record):
        msg = self.format(record)
        try:
            self.tui_logger.post_message(self.NewLog(msg))
        except Exception:
            self.handleError(record)


class ProcessMonitor(App):
    CSS = """
    Tree > .selected-group {
        color: yellow;
        text-style: bold;
    }
    Tree > .selected-process {
        color: yellow;
    }
    #log-widget {
        height: 20%;
    }
    """

    BINDINGS = [
        Binding("g", "change_group_by", "Change Group By"),
        Binding("s", "toggle_sort", "Toggle Sort"),
        Binding("f", "filter", "Filter"),
        Binding("t", "toggle_group", "Toggle Group"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, monitor: RemoteProcessMonitor):
        super().__init__()
        self.monitor = monitor
        self.logger = Log(id="log-widget", max_lines=50)
        self.process_tree = ProcessTree(monitor, self.logger)

    @on(TuiLogHandler.NewLog)
    def handle_new_log(self, message: TuiLogHandler.NewLog) -> None:
        """
        These messages are bubbled up from the TUILogHandler.
        """
        self.logger.write_line(message.msg)

    def on_mount(self) -> None:
        # Attach TUI log handler
        tui_log_handler = TuiLogHandler(self.logger)
        tui_log_handler.setLevel(logging.DEBUG)
        tui_log_handler.setFormatter(FORMATTER)

        # Add handler to root logger to capture all logs
        root_logger = logging.getLogger()
        root_logger.addHandler(tui_log_handler)
        self.post_message(TuiLogHandler.NewLog("[Log Area]"))

    def compose(self) -> ComposeResult:
        yield Header()
        yield self.process_tree
        yield self.logger
        yield Footer()

    async def action_change_group_by(self) -> None:
        await self.process_tree.change_group_by()

    async def action_toggle_sort(self) -> None:
        await self.monitor.cleanup()
        await self.process_tree.toggle_sort()

    def action_filter(self) -> None:
        self.app.push_screen(FilterScreen(self.process_tree))

    async def action_toggle_group(self) -> None:
        node = self.process_tree.cursor_node
        if node.is_root:
            return
        try:
            is_group = node.data.get("is_group")
        except Exception:
            is_group = False
        if is_group:
            # Convert Text object to string if needed
            label = node.label.plain if hasattr(node.label, "plain") else str(node.label)
            await self.process_tree.toggle_group(label)
        else:
            # If it's a process node, toggle its parent group
            pid = node.data.get("pid", None)
            if pid:
                await self.process_tree.toggle_process(pid)
            # parent = node.parent
            # if parent:
            #     # Convert Text object to string if needed
            #     label = parent.label.plain if hasattr(parent.label, "plain") else str(parent.label)
            #     await self.process_tree.toggle_group(label)

    async def on_unmount(self) -> None:
        """Clean up resources when the app is closed."""
        await self.monitor.cleanup()


class FilterScreen(App):
    def __init__(self, process_tree: ProcessTree):
        super().__init__()
        self.process_tree = process_tree

    def compose(self) -> ComposeResult:
        yield Static("Enter filter text:")
        yield Static(self.process_tree.filter_text)

    async def on_key(self, event):
        if event.key == "escape":
            self.app.pop_screen()
        elif event.key == "enter":
            await self.process_tree.set_filter(self.process_tree.filter_text)
            self.app.pop_screen()
        else:
            self.process_tree.filter_text += event.character
