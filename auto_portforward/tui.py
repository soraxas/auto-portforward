#!/usr/bin/python
import asyncio
import logging
import os
import subprocess
import signal
from typing import Dict, Set
from textual import work
from textual.app import App, ComposeResult
from textual.message_pump import Timer
from textual.widgets import Header, Footer, Static, Tree
from textual.binding import Binding
from textual.widgets import Log


# from textual.style import Style
from rich.style import Style

from auto_portforward.process_provider.abstract_provider import AbstractProvider

from .process_provider.ssh_remote import RemoteProcessMonitor
from .datatype import Process

# Configure logging
log_dir = os.path.expanduser("logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "tui.log")

# Create formatters
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Add file handler for main log
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

LOGGER = logging.getLogger("tui")


def set_process_group():
    """Set the process group ID to the current process ID."""
    os.setpgrp()


class ProcessTree(Tree):
    """
    A tree of processes.
    """

    def __init__(self, monitor: AbstractProvider, logger: Log):
        super().__init__("Processes")
        self.monitor: AbstractProvider = monitor
        self.last_memory: Dict[str, Process] = {}
        self.selected_groups: Set[str] = set()
        self.group_by = "cwd"
        self.sort_reverse = False
        self.filter_text = ""
        self.update_interval = 1.0
        self.last_update = 0
        self.forwarded_ports: Dict[int, subprocess.Popen] = {}
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
            is_selected = group_key in self.selected_groups

            # Create group node
            group_node = self.root.add(group_key, expand=True)
            group_node.data = {"is_group": True}

            selected_style = Style(color="yellow", italic=True)
            if is_selected:
                group_node.label.style = selected_style

            # Sort processes
            sorted_processes = sorted(
                processes,
                key=lambda p: str(p.pid) if p.pid is not None else "0",
                reverse=self.sort_reverse,
            )

            for process in sorted_processes:
                ports = process.ports
                ports_str = f" [Ports: {', '.join(map(str, ports))}]" if ports else ""
                process_str = f"PID: {process.pid} - {process.name} - {process.status}{ports_str}"

                # Add process node
                process_node = group_node.add_leaf(process_str)
                process_node.data = {"is_group": False}
                if is_selected:
                    process_node.label.style = selected_style

                    # Add ports to forward
                    for p in ports:
                        ports_to_forward.add(p)
        asyncio.create_task(self.manage_ports_forwarding(ports_to_forward))

    async def manage_ports_forwarding(self, ports_to_forward: Set[int]) -> None:
        # Remove old port forwards
        for existing in list(self.forwarded_ports.keys()):
            if existing not in ports_to_forward:
                process = self.forwarded_ports.pop(existing)
                try:
                    process.terminate()
                    # # Kill the entire process group
                    # os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    process.wait(timeout=5)
                    LOGGER.info("Terminated port forwarding for port %s", existing)
                except subprocess.TimeoutExpired:
                    LOGGER.warning(
                        "Port forwarding process for port %s did not terminate gracefully, forcing...",
                        existing,
                    )
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception as e:
                    LOGGER.error("Error terminating port forwarding for port %s: %s", existing, e)
        # Start new port forwards
        for p in ports_to_forward:
            if p not in self.forwarded_ports:
                # LOGGER.debug("Adding port to forward: %s", p)
                try:
                    # Start the reverse_port subprocess with process group
                    process = subprocess.Popen(
                        ["reverse_port.sh", "fait", str(p)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        preexec_fn=set_process_group,
                    )
                    self.forwarded_ports[p] = process
                    LOGGER.info(
                        "Started port forwarding for port %s with PID %s",
                        p,
                        process.pid,
                    )
                except Exception as e:
                    LOGGER.error("Failed to start port forwarding for port %s: %s", p, e)

    def on_unmount(self) -> None:
        """Clean up all port forwarding processes when the widget is removed."""

        if self.regular_update_timer:
            self.regular_update_timer.stop()

        for port, process in self.forwarded_ports.items():
            try:
                # Kill the entire process group
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
                LOGGER.debug("Terminated port forwarding for port %s during cleanup", port)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception as e:
                LOGGER.error(
                    "Error terminating port forwarding for port %s during cleanup: %s",
                    port,
                    e,
                )

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


class TUILogHandler(logging.Handler):
    def __init__(self, tui_logger):
        super().__init__()
        self.tui_logger = tui_logger

    def emit(self, record):
        msg = self.format(record)
        self.tui_logger.write_line(msg)


class ProcessMonitor(App):
    CSS = """
    Tree > .selected-group {
        color: yellow;
        text-style: bold;
    }
    Tree > .selected-process {
        color: yellow;
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
        self.logger = Log()
        self.process_tree = ProcessTree(monitor, self.logger)

        # Attach TUI log handler
        tui_log_handler = TUILogHandler(self.logger)
        tui_log_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        tui_log_handler.setFormatter(formatter)
        LOGGER.addHandler(tui_log_handler)

    def compose(self) -> ComposeResult:
        yield Header()
        yield self.process_tree
        yield self.logger
        yield Footer()

    async def action_change_group_by(self) -> None:
        await self.process_tree.change_group_by()

    async def action_toggle_sort(self) -> None:
        await self.process_tree.toggle_sort()

    def action_filter(self) -> None:
        self.app.push_screen(FilterScreen(self.process_tree))

    async def action_toggle_group(self) -> None:
        node = self.process_tree.cursor_node
        if node.is_root:
            return
        try:
            LOGGER.debug("data: %s", node)
            is_group = node.data.get("is_group")
        except Exception:
            is_group = False
        LOGGER.debug("is_group: %s", is_group)
        if is_group:
            # Convert Text object to string if needed
            label = node.label.plain if hasattr(node.label, "plain") else str(node.label)
            await self.process_tree.toggle_group(label)
        else:
            # If it's a process node, toggle its parent group
            parent = node.parent
            if parent:
                # Convert Text object to string if needed
                label = parent.label.plain if hasattr(parent.label, "plain") else str(parent.label)
                await self.process_tree.toggle_group(label)

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
