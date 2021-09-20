# vmctrlcli
Control virtual units through commands in input.

## Disclaimer
The script is required to run as root. Any responsibility for the usage of this script is up to the user. The developer(s) of this script cannot be charged for any claim, no matter the usage of this script.

## Reason
I needed a better script to manage virtual machines/containers(referred to as "units") in Proxmox with `apcupsd`. As events from the UPS could overlap, the state of a unit would end up undetermined. This script would then remove this race-condition by effectively placing all tasks in a queue(Like how Proxmox does it) and abort items in the queue if the state was changed before executed(i.e. first send command to stop 10 units, then after 3 of them has been stopped(fourth has just started), tell it to start them again, effectively removing 6 of the items from the queue, waits for the fourth to finish and then starts them all 4).

The script is made in such a way that it would be easy to modify it for other hosts not being Proxmox.

## Install
Install python3. Download and put the script somewhere where it can be called from.

### Recommendation
Run the script as a daemon with `systemd` with an included fifo-pipe for stdin. Then send commands to it from the pipe-file created. The script does not have a regular prompt, so this is the best way to communicate with it.

## Usage
Run the script and you may now input commands. Each command is issued with an enter.
- `start [vmid/name ...]` Start all units that has the `onboot` flag enabled. Providing any number of vmid or name will start those units specifically, ignoring the `onboot` flag.
- `stop [vmid/name ...]` Stop all units. Providung any number of vmid or name will stop those units specifically.
- `list` List all units.
  - `list running` List the running units.
  - `list onboot` List units that are started on boot.
