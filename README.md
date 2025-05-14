# auto portforward

<div align="center">
  <img src="https://raw.githubusercontent.com/soraxas/auto-portforward/refs/heads/master/assets/screenshot.png" alt="auto-portforward">
</div>

## Overview

**auto-portforward** is a Python-based TUI application for monitoring processes and managing port forwarding on local or remote (SSH) machines. It provides a real-time, interactive process tree and can automatically set up SSH reverse port forwarding for processes listening on specific ports.

## Features

- TUI interface
- Monitor processes and their listening ports on local or remote (SSH)
- Automatic SSH reverse port forwarding for selected ports
- Works with or without [psutil](https://pypi.org/project/psutil/) (falls back to lsof/ps)
- Handles sudo password for privileged commands
- Clean resource management (no zombie processes, robust cleanup)

## Installation

```sh
pipx install auto-portforward
```

## Usage

Run the TUI application with:

```sh
auto-portforward [options] [ssh_host]
```

### CLI Options

- `-l`, `--local` : Use the local process monitor
- `--mock`        : Use a mock process monitor (for testing)
- `[ssh_host]`    : SSH host to connect to

Examples:

- Monitor local machine:
  ```sh
  python -m auto_portforward.cli --local
  ```
- Monitor a remote host via SSH:
  ```sh
  python -m auto_portforward.cli myuser@myhost
  ```

### Sudo Password Handling

Some features (like listing all listening ports) may require sudo privileges.

- **Environment variable:**
  ```sh
  export AP_SUDO_PASSWORD=yourpassword
  auto-portforward my@host
  ```

