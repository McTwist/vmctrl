#!/usr/bin/env python3

import sys
from subprocess import Popen, PIPE
from threading import Thread, Condition, Event

DRY = False

def str_to_dict(s):
	return dict(item.split("=") for item in s.split(","))

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
	def config(vmid):
		return program("qm", "config", vmid, stdout=PIPE)
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
	@staticmethod
	def config(vmid):
		return program("pct", "config", vmid, stdout=PIPE)
	@staticmethod
	def list():
		return program("pct", "list", stdout=PIPE)

class VirtualUnit:
	def __init__(self, prgm, vmid, *, name="", status="unknown"):
		self.__prgm = prgm
		self.vmid = vmid
		self.name = name
		self.status = status
		# Cache
		self.__config = {}
	def start(self):
		if self.status == "running":
			return
		if DRY:
			print("started {}".format(self.name or self.vmid))
			return
		proc = self.__prgm.start(self.vmid)
		proc.wait()
		self.status = "running"
	def shutdown(self):
		if self.status == "stopped":
			return
		if DRY:
			print("stopped {}".format(self.name or self.vmid))
			return
		proc = self.__prgm.shutdown(self.vmid)
		proc.wait()
		self.status = "stopped"
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
		return self.status == "running"
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
		return None
	def delay_up(self):
		config = self.config()
		if "startup" in config:
			startup = str_to_dict(config["startup"])
			if "up" in startup:
				return int(startup["up"])
		return None
	def __eq__(self, other):
		return self.vmid == other

class Daemon:
	def __init__(self, *args, **kwargs):
		self.__lock = Condition()
		self.__queue = []
		self.__sleep = Event()
		self.__run = True
		self.__thread = Thread(target=self.run, args=args, kwargs=kwargs)
		self.__thread.start()
	def run(self):
		while self.__run:
			(cmd, unit) = self.get()
			if cmd == "start":
				unit.start()
				delay = unit.delay_up()
				if delay:
					self.__sleep.wait(timeout=delay)
			elif cmd == "shutdown":
				unit.shutdown()
	def abort(self):
		self.__run = False
		with self.__lock:
			self.__queue.insert(0, ("dummy", None))
			self.__lock.notify_all()
		self.__sleep.set()
		self.__thread.join()
		self.__queue = []
	def add(self, cmd, unit):
		with self.__lock:
			for i in range(len(self.__queue)):
				(c, v) = self.__queue[i]
				if v == unit:
					# Ignore consecutive identical calls
					if c != cmd:
						print("Abort {} on {}".format(c, unit.name or unit.vmid))
						del self.__queue[i]
					return
			self.__queue.append((cmd, unit))
			self.__lock.notify()
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
		l = [u for u in virtual_get_onboot() if u.status != "running"]
	else:
		l = []
		for vm in vms:
			u = virtual_find(vm)
			if u is None:
				continue
			if u.status != "running":
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
	daemon = Daemon()
	try:
		while True:
			try:
				line = sys.stdin.readline()
			except KeyboardInterrupt:
				break
			except Exception as e:
				print(e)
				continue
			if not line:
				break
			cmds = line.split()
			if len(cmds) == 0:
				continue
			(cmd, *args) = cmds
			if cmd == "start":
				for u in virtual_prepare_start(args):
					daemon.add("start", u)
			elif cmd == "shutdown":
				for u in virtual_prepare_shutdown(args):
					daemon.add("shutdown", u)
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
