#!/usr/bin/env python3

import sys
from subprocess import Popen, PIPE
from threading import Thread, Condition, Event

DRY = False

class Status:
	UNKNOWN = -1
	STOPPED = 0
	RUNNING = 1
	PAUSED = 2

class Cmd:
	UNKNOWN = -1
	START = 0
	SHUTDOWN = 1
	RESUME = 2
	SUSPEND = 3
	HIBERNATE = 4
	STOP = 5

str_status = {
	"stopped": Status.STOPPED,
	"running": Status.RUNNING,
	"paused": Status.PAUSED
}

str_cmd = {
	"start": Cmd.START,
	"shutdown": Cmd.SHUTDOWN,
	"resume": Cmd.RESUME,
	"suspend": Cmd.SUSPEND,
	"hibernate": Cmd.HIBERNATE,
	"stop": Cmd.STOP
}

def inv_dict(d):
	return {v: k for k, v in d.items()}

status_str = inv_dict(str_status)
cmd_str = inv_dict(str_cmd)

def str_to_dict(s):
	return dict(item.split("=") for item in s.split(","))

class Sleep:
	def __init__(self):
		self.__event = Event()
	def __call__(self, timeout):
		self.__event.wait(timeout=timeout)
	def wake(self):
		self.__event.set()
	def clear(self):
		self.__event.clear()

def program(*args, **kwargs):
	return Popen([*args], **kwargs)

class qm:
	@staticmethod
	def start(vmid):
		return program("qm", "start", vmid)
	@staticmethod
	def shutdown(vmid):
		return program("qm", "shutdown", vmid)
	@staticmethod
	def resume(vmid):
		return program("qm", "resume", vmid)
	@staticmethod
	def suspend(vmid):
		return program("qm", "suspend", vmid)
	@staticmethod
	def hibernate(vmid):
		return program("qm", "suspend", vmid, "--todisk", "1")
	@staticmethod
	def stop(vmid):
		return program("qm", "stop", vmid)
	@staticmethod
	def config(vmid):
		return program("qm", "config", vmid, stdout=PIPE)
	@staticmethod
	def status(vmid):
		return program("qm", "status", vmid, stdout=PIPE)
	@staticmethod
	def list():
		return program("qm", "list", stdout=PIPE)

class pct:
	@staticmethod
	def start(vmid):
		return program("pct", "start", vmid)
	@staticmethod
	def shutdown(vmid):
		return program("pct", "shutdown", vmid)
	# Note: Containers are unable to suspend due to a bug
	@classmethod
	def resume(cls, vmid):
		return cls.start(vmid)
	@classmethod
	def suspend(cls, vmid):
		return cls.shutdown(vmid)
	@classmethod
	def hibernate(cls, vmid):
		return cls.shutdown(vmid)
	@staticmethod
	def stop(vmid):
		return program("pct", "stop", vmid)
	@staticmethod
	def config(vmid):
		return program("pct", "config", vmid, stdout=PIPE)
	@staticmethod
	def status(vmid):
		return program("pct", "status", vmid, stdout=PIPE)
	@staticmethod
	def list():
		return program("pct", "list", stdout=PIPE)

class VirtualUnit:
	def __init__(self, prgm, vmid, *, name="", status=Status.UNKNOWN):
		self.__prgm = prgm
		self.vmid = vmid
		self.name = name
		self.status = status
		# Cache
		self.__config = {}
	def __change_state(self, new_status, old_status, cmd):
		if self.status in old_status:
			return
		if DRY:
			print("{} {}".format(status_str[new_status], self.name or self.vmid))
			return
		proc = cmd(self.vmid)
		proc.wait()
		self.status = new_status
	def start(self):
		self.__change_state(Status.RUNNING, [Status.RUNNING], self.__prgm.start)
	def shutdown(self):
		self.__change_state(Status.STOPPED, [Status.STOPPED], self.__prgm.shutdown)
	def resume(self):
		self.__change_state(Status.RUNNING, [Status.RUNNING], self.__prgm.resume)
	def suspend(self):
		self.__change_state(Status.PAUSED, [Status.STOPPED, Status.PAUSED], self.__prgm.suspend)
	def hibernate(self):
		self.__change_state(Status.STOPPED, [Status.STOPPED], self.__prgm.hibernate)
	def stop(self):
		self.__change_state(Status.STOPPED, [Status.STOPPED], self.__prgm.stop)
	def config(self, *, force=False):
		if not force and len(self.__config):
			return self.__config
		proc = self.__prgm.config(self.vmid)
		proc.stdout.readline()
		config = {}
		for line in proc.stdout.readlines():
			(key, val) = line.decode().strip().split(": ")
			config[key] = val
		proc.wait()
		self.__config = config
		return config
	def running(self):
		return self.status == Status.RUNNING
	def onboot(self):
		config = self.config()
		if "onboot" in config:
			return config["onboot"] == "1"
		return False
	def order(self):
		config = self.config()
		if "startup" in config:
			startup = str_to_dict(config["startup"])
			if "order" in startup:
				return int(startup["order"])
		return 2**10000 # Infinite enough
	def delay_up(self):
		config = self.config()
		if "startup" in config:
			startup = str_to_dict(config["startup"])
			if "up" in startup:
				return int(startup["up"])
		return None
	def __eq__(self, other):
		return self.vmid == other

class UnitAction:
	def __init__(self, cmd, unit, run):
		self.cmd = cmd
		self.unit = unit
		self.__run = run

	def __call__(self):
		return self.__run(self.unit)

	def __eq__(self, other):
		return self.unit == other

	def should_cancel(self, action):
		return self.cmd != action.cmd

class Daemon:
	def __init__(self, *args, **kwargs):
		self.__lock = Condition()
		self.__queue = []
		self.__sleep = Sleep()
		self.__run = True
		self.__thread = Thread(target=self.run, args=args, kwargs=kwargs)
		self.__thread.start()
	def run(self):
		while self.__run:
			action = self.get()
			delay = action()
			if delay:
				self.__sleep(delay)
	def abort(self):
		self.__run = False
		with self.__lock:
			self.__queue = [lambda: None]
			self.__lock.notify_all()
		self.__sleep.wake()
		self.__thread.join()
		self.__queue = []
	def add(self, action):
		with self.__lock:
			self.__queue.append(action)
			self.__lock.notify()
	def try_cancel(self, action):
		with self.__lock:
			for i in range(len(self.__queue)):
				a = self.__queue[i]
				if a == action:
					if a.should_cancel(action):
						print("Abort {} on {}".format(cmd_str[a.cmd], a.unit.name or a.unit.vmid))
						del self.__queue[i]
					return True
		return False
	def get(self):
		with self.__lock:
			while not len(self.__queue):
				self.__lock.wait()
			val = self.__queue[0]
			del self.__queue[0]
			return val

def vm_list():
	proc = qm.list()
	proc.stdout.readline()
	for line in proc.stdout.readlines():
		line = line.decode().strip().split()
		if len(line) != 6:
			print("Unable to parse line: '{}'".format(line))
			continue
		(vmid, name, status, *_) = line
		status = qm.status(vmid) # List is lying(paused=running), so check manually
		status = str_status[status] if status in str_status else Status.UNKNOWN
		yield VirtualUnit(qm, vmid, name=name, status=status)
	proc.wait()

def ct_list():
	proc = pct.list()
	proc.stdout.readline()
	for line in proc.stdout.readlines():
		line = line.decode().strip().split()
		if len(line) == 4:
			(vmid, status, _, name) = line
		elif len(line) == 3:
			(vmid, status, name) = line
		else:
			print("Unable to parse line: '{}'".format(line))
			continue
		status = str_status[status] if status in str_status else Status.UNKNOWN
		yield VirtualUnit(pct, vmid, name=name, status=status)
	proc.wait()

def virtual_get_all():
	for c in ct_list():
		yield c
	for v in vm_list():
		yield v

def virtual_get_onboot():
	for u in virtual_get_all():
		if u.onboot():
			yield u

def virtual_get_running():
	for u in virtual_get_all():
		if u.running():
			yield u

def virtual_find(arg):
	for c in ct_list():
		if c.vmid == arg or c.name == arg:
			return c
	for v in vm_list():
		if v.vmid == arg or v.name == arg:
			return v
	return None

def virtual_prepare_start(vms=[]):
	if vms is []:
		l = [u for u in virtual_get_onboot()]
	else:
		l = []
		for vm in vms:
			u = virtual_find(vm)
			if u is None:
				continue
			l.append(u)
	return sorted(l, key=VirtualUnit.order)

def virtual_prepare_shutdown(vms=[]):
	if vms is []:
		l = [u for u in virtual_get_running()]
	else:
		l = []
		for vm in vms:
			u = virtual_find(vm)
			if u is None:
				continue
			l.append(u)
	return sorted(l, key=VirtualUnit.order, reverse=True)

def main(argv):
	def start_delay(unit):
		unit.start()
		return unit.delay_up()
	actions = {
		Cmd.START:     (virtual_prepare_start, start_delay),
		Cmd.SHUTDOWN:  (virtual_prepare_shutdown, VirtualUnit.shutdown),
		Cmd.RESUME:    (virtual_prepare_start, VirtualUnit.resume),
		Cmd.SUSPEND:   (virtual_prepare_shutdown, VirtualUnit.suspend),
		Cmd.HIBERNATE: (virtual_prepare_shutdown, VirtualUnit.hibernate),
		Cmd.STOP:      (virtual_prepare_shutdown, VirtualUnit.stop)
	}
	states = {}
	daemon = Daemon()
	try:
		while True:
			try:
				line = sys.stdin.readline()
			except Exception as e:
				print(e)
				continue
			if not line:
				break
			cmds = line.split()
			if len(cmds) == 0:
				continue
			(cmd, *args) = cmds
			# Command to one or more units
			if cmd in str_cmd:
				action = str_cmd[cmd]
				for u in actions[action][0](args):
					a = UnitAction(action, u, actions[action][1])
					if not daemon.try_cancel(a):
						daemon.add(a)
			elif cmd == "save":
				if len(args) == 0:
					continue
				if args[0] in states:
					print("State {} already exist".format(args[0]))
					continue
				state = []
				for u in virtual_prepare_shutdown(args[1:]):
					state.append(u.vmid)
				states[args[0]] = state
			elif cmd == "load":
				if len(args) == 0:
					continue
				if args[0] not in states:
					print("State {} does not exist".format(args[0]))
					continue
				state = states[args[0]]
				for u in virtual_prepare_start(state):
					a = UnitAction(Cmd.START, u, start_delay)
					if not daemon.try_cancel(a):
						daemon.add(a)
				del states[args[0]]
			elif cmd == "list":
				# Only for debugging
				if len(args) == 0:
					l = virtual_get_all()
				elif args[0] == "running":
					l = virtual_get_running()
				elif args[0] == "onboot":
					l = virtual_get_onboot()
				for a in l:
					print("{}, {}".format(a.vmid, a.name))
	except KeyboardInterrupt:
		pass
	daemon.abort()
	return 0

if __name__ == "__main__":
	sys.exit(main(sys.argv))
