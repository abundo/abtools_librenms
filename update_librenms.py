#!/usr/bin/env python3

"""
Syncs devices in librenms, based on Device-API
    If device is missing in librenms, add it
    If device exist in librenms but not in Device-API remove it
    Check and adjust all Enabled flags on devices and device interfaces

    note:
    - device API uses enabled true/false for devices and interfaces
    - librenms uses Ignore alerts: true/false for devices and interfaces
"""
import sys
import re

# ----- Start of configuration items -----

CONFIG_FILE="/etc/abtools/abtools_librenms.yaml"

# ----- End of configuration items -----

import yaml
import ipaddress
from orderedattrdict import AttrDict

sys.path.insert(0, "/opt")
import ablib.utils as utils
from ablib.librenms import Librenms_Mgr
from ablib.devices import Device_Mgr

# Load configuration
config = utils.load_config(CONFIG_FILE)

# Compile regex for faster search
roles_enabled_compiled = []
for role in config.librenms_sync.roles_enabled:
    roles_enabled_compiled.append(re.compile(role))

interfaces_disabled_compiled = []
for interface in config.librenms_sync.interfaces_disabled:
    interfaces_disabled_compiled.append(re.compile(interface))


def sync_interfaces(name, librenms_mgr, librenms_device, api_device):
    # Get interfaces from librenms
    librenms_interfaces = librenms_mgr.get_device_interfaces(name)
    if librenms_interfaces is None:
        print("  Error: No interfaces in Librenms found")
        return
    
    api_device_interfaces = api_device['interfaces']

    # Default for all ports comes from device api, "alarm_interfaces"
    # If device is from netbox
    #    ignore = netbox custom field "alarm_interfaces"
    #    if interface has tag uplink or tag "librenms_alert_enable", ignore = 0
    #    if interface has tag "librenms_alert_disabled", ignore = 1
    # If device is from BECS
    #    ignore = 0
    #    if interface has role uplink.* then ignore = 1
    alarm_interfaces = api_device["alarm_interfaces"]
    for librenms_interface in librenms_interfaces.values():

        if librenms_interface.ifname in api_device_interfaces:
            librenms_interface_name = librenms_interface.ifname
        elif librenms_interface.ifalias in api_device_interfaces:
            librenms_interface_name = librenms_interface.ifalias
        elif librenms_interface.ifdescr in api_device_interfaces:
            librenms_interface_name = librenms_interface.ifdescr
        else:
            librenms_interface_name = None
            #print("Librenms interface %s in %s does not exist in Device-API" % (name, librenms_interface_name))

        if api_device["alarm_interfaces"]:  # Default for interfaces in this device
            ignore = 0
        else:
            ignore = 1
        role = ""

        if librenms_interface_name:
            api_device_interface = api_device_interfaces[librenms_interface_name]

            if "uplink" in api_device_interface["tags"]:
                ignore = 0

            if "librenms_alarm_disable" in api_device_interface["tags"]:
                ignore = 1

            if "librenms_alarm_enable" in api_device_interface["tags"]:
                ignore = 0

            if "role" in api_device_interface:
                role = api_device_interface["role"]
                for role_regex in roles_enabled_compiled:
                    if role_regex.search(role):
                        ignore = 0

            for interface_regex in interfaces_disabled_compiled:
                # print(librenms_interface_name, interface_regex)
                if interface_regex.search(librenms_interface_name):
                    ignore = 1

        if librenms_interface.ignore != ignore:
            if role:
                role = "role %s, " % role
            print("    Name %s, interface %s%s, setting ignore to %s" % (name, librenms_interface.ifname, role, ignore))
            d = AttrDict()
            d.ignore = ignore
            librenms_mgr.update_device_interface(port_id=librenms_interface.port_id, data=d)


def main():
    librenms_mgr = Librenms_Mgr(config=config.librenms)
    librenms_devices = librenms_mgr.get_devices()
    
    device_mgr = Device_Mgr(config=config.devices)
    api_devices = device_mgr.get_devices()

    create_in_librenms = []
    delete_in_librenms = []

    print("-" * 79)
    print("Device count")
    print("  Device-API:")
    print("    Devices    : %5d devices" % len(api_devices))
    print("    Persistent : %5d devices" % len(config.librenms_sync.persistent_devices))
    print("    Total      : %5d devices" % (len(api_devices) + len(config.librenms_sync.persistent_devices)))
    print()
    print("  Librenms:")
    print("    Devices    : %5d devices" % len(librenms_devices))
    print()
    
    print("-" * 79)
    print("Update /etc/hosts file")
    utils.write_etc_hosts_file(api_devices)
    print()

    #
    # Compare Devices-API with devices in Librenms
    #
    
    print("-" * 79)
    print("Checking")
    
    print("  Devices that exist in Device-API but not in Librenms (action: create in librenms):")
    for name, device in api_devices.items():
        if not device["enabled"]:
            continue
        if not device["monitor_librenms"]:
            continue
        if not name in librenms_devices:
            print("   ", name)
            create_in_librenms.append(name)
    if len(create_in_librenms) < 1:
        print("    None")

    print("  device that exist in Librenms but not in Device-API. (action: delete from librenms)")
    for name, device in librenms_devices.items():
        if name in config.librenms_sync.persistent_devices:
            continue
        api_device = api_devices.get(name, None)
        if api_device is None:
            # Does not exist in Device-API, delete
            delete_in_librenms.append(name)
            continue
        if not api_device["enabled"]:
            # Not enabled in Device-API, delete
            delete_in_librenms.append(name)
            continue

    if len(delete_in_librenms) < 1:
        print("    None")
        
    #
    # Add/delete devices
    #
    
    print()
    print("-" * 79)
    print("Adjust devices in Librenms")
    print("  Creating devices in Librenms")
    if len(create_in_librenms):
        for name in create_in_librenms:
            print("   ", name)
            librenms_mgr.create_device(name=name, force_add=1)
    else:
        print("    None")
    
    print("  Deleting devices in Librenms")
    if len(delete_in_librenms):
        for name in delete_in_librenms:
            print("   ", name)
            librenms_mgr.delete_device(name=name)
    else:
        print("    None")

    #
    # Update status on devices and ports in Librenms
    #
    print("  Updating devices in Librenms")
    if create_in_librenms or delete_in_librenms:
        # devices has been added/deleted, reload list of devices in librenms
        librenms_mgr.load_devices()

    for name, librenms_device in librenms_devices.items():
        if name not in api_devices:
            print("    Ignoring %s, not in Device-API" % name)
            continue
        api_device = api_devices[name]
        if api_device:
            data = AttrDict()
             
            librenms_enabled = librenms_device['ignore'] == 0
            api_enabled = api_device['enabled']
            if librenms_enabled != api_enabled:
                if api_enabled:
                    data.ignore = 0
                else:
                    data.ignore = 1
                print(f"    Name {name}, setting ignore to {data.ignore}")
            
            if len(data):
                ret = librenms_mgr.update_device(name, data)

            sync_interfaces(name, librenms_mgr, librenms_device, api_device)
    

if __name__ == '__main__':
    try:
        main()
    except:
        # Error in script, send traceback to developer
        utils.send_traceback()
