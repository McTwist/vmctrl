#!/usr/bin/env python3

import sys

def main(argv):
	with open("/run/vmctrld.in", "a") as f:
		f.write(" ".join(argv[1:]))
		f.write("\n")
		f.flush()

if __name__ == "__main__":
	sys.exit(main(sys.argv))
