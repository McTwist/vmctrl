#!/bin/bash

cp vmctrld.py /usr/local/sbin/vmctrld
cp vmctrlctl.py /usr/local/sbin/vmctrlctl
cp vmctrld.service vmctrld.socket /etc/systemd/system/
systemctl daemon-reload
systemctl restart vmctrld
