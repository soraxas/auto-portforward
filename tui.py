#!/usr/bin/python
import asyncio
import time
import logging
import os
import sys
from typing import Dict, Set, List, Tuple
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, Static, Tree
from textual.widgets.tree import TreeNode
from textual.binding import Binding
from textual.reactive import reactive
# from textual.style import Style
from rich.style import Style


from remote_process_monitor import RemoteProcessMonitor
from datatype import Process

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

class ProcessTree(Tree):
    def __init__(self, remote_monitor: RemoteProcessMonitor):
        super().__init__("Processes")
        self.remote_monitor = remote_monitor
        self.last_memory: Dict[int, Process] = {}
        self.selected_groups: Set[str] = set()
        self.group_by = "cwd"
        self.sort_reverse = False
        self.filter_text = ""
        self.update_interval = 1.0
        self.last_update = 0

    def on_mount(self) -> None:
        def expand_all(node):
            node.expand()
            for child in node.children:
                (child)

        expand_all(self.root)
        self.set_interval(self.update_interval, self.update_processes)

    def is_new_memory(self, new_memory: Dict[int, Process]) -> bool:
        if not new_memory:
            return False
        if not self.last_memory:
            return True
        if len(new_memory) != len(self.last_memory):
            return True
        for pid, process in new_memory.items():
            if process != self.last_memory[pid]:
                return True
        return False

    async def update_processes(self) -> None:
        new_memory = await self.remote_monitor.get_remote_processes()
        if not new_memory:
            return

        if not self.is_new_memory(new_memory):
            return

        self.last_memory = new_memory
        self.clear()

        await self.update_process_layout()

    async def toggle_group(self, group_key: str) -> None:
        LOGGER.debug("Toggling group: %s", group_key)
        # Convert Text object to string if needed
        if hasattr(group_key, 'plain'):
            group_key = group_key.plain
        if group_key in self.selected_groups:
            self.selected_groups.remove(group_key)
        else:
            self.selected_groups.add(group_key)
        await self.update_process_layout()

    async def update_process_layout(self) -> None:
        # Group processes
        grouped = {}
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
            reverse=self.sort_reverse
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
                reverse=self.sort_reverse
            )

            for process in sorted_processes:
                ports = self.remote_monitor.connections.get(str(process.pid), [])
                ports_str = f" [Ports: {', '.join(map(str, ports))}]" if ports else ""
                process_str = f"PID: {process.pid} - {process.name} - {process.status}{ports_str}"

                # Add process node
                process_node = group_node.add_leaf(process_str)
                process_node.data = {"is_group": False}
                if is_selected:
                    process_node.label.style = selected_style

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

    def __init__(self, remote_monitor: RemoteProcessMonitor):
        super().__init__()
        self.remote_monitor = remote_monitor
        self.process_tree = ProcessTree(remote_monitor)

    def compose(self) -> ComposeResult:
        yield Header()
        yield self.process_tree
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
        except:
            is_group = False
        LOGGER.debug("is_group: %s", is_group)
        if is_group:
            # Convert Text object to string if needed
            label = node.label.plain if hasattr(node.label, 'plain') else str(node.label)
            await self.process_tree.toggle_group(label)
        else:
            # If it's a process node, toggle its parent group
            parent = node.parent
            if parent:
                # Convert Text object to string if needed
                label = parent.label.plain if hasattr(parent.label, 'plain') else str(parent.label)
                await self.process_tree.toggle_group(label)

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

def main():
    ssh_host = sys.argv[1] if len(sys.argv) > 1 else "soraxas@fait"
    LOGGER.debug("Starting TUI with SSH host: %s", ssh_host)

    # First establish the connection
    remote_monitor = RemoteProcessMonitor(ssh_host)
    if not remote_monitor.connect():
        LOGGER.error("Failed to establish connection. Exiting.")
        sys.exit(1)

    try:
        app = ProcessMonitor(remote_monitor)
        app.run()
    finally:
        remote_monitor.cleanup()

if __name__ == "__main__":
    main()
