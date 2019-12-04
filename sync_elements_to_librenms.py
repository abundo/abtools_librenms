#!/usr/bin/env python3

"""
Syncs devices in librenms, based on Element API
    If element is missing in librenms, add it
    If element exist in librenms but not in element API remove it
    Check and adjust all "ignore" on elements and element interfaces
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
from ablib.elements import Elements_Mgr

# Load configuration
config = utils.load_config(CONFIG_FILE)

create_in_librenms = []
delete_in_librenms = []

# Compile regex for faster search
roles_enabled_compiled = []
for role in config.librenms_sync.roles_enabled:
    roles_enabled_compiled.append(re.compile(role))

interfaces_disabled_compiled = []
for interface in config.librenms_sync.interfaces_disabled:
    interfaces_disabled_compiled.append(re.compile(interface))


def sync_interfaces(librenms_mgr, api_elements, hostname):
    # Get interfaces from librenms
    librenms_intf = librenms_mgr.get_element_interfaces(hostname)
    if librenms_intf is None:
        print("  Error: No interfaces in Librenms found")
        return
    
    if not hostname in api_elements:
        print("  Error: hostname missing in api_elements")
        return
        
    element_intf = api_elements[hostname]['interfaces']
    
    # Default for all ports comes from element api
    # If element is from netbox
    #    ignore = netbox custom field "default_interface_alarm"
    #    if interface has tag uplink, ignore = 0
    #    if interface has tag ignore, ignore = 1
    # If element is from BECS
    #    ignore = 0
    #    if interface has role uplink.* then ignore = 1
    for librenms_interface_name, librenms_interface in librenms_intf.items():
        ignore = 0  # default
        role = "?"
        if librenms_interface_name in element_intf:
            element_interface = element_intf[librenms_interface_name]
            if 'role' in element_interface:
                role = element_interface['role']
                for role_regex in roles_enabled_compiled:
                    if role_regex.search(role) is None:
                        ignore = 1
            for interface_regex in interfaces_disabled_compiled:
                # print(librenms_interface_name, interface_regex)
                if interface_regex.search(librenms_interface_name):
                    ignore = 1

        if librenms_interface.ignore != ignore:
            print("    Hostname %s, interface %s role %s, setting ignore to %s" % (hostname, librenms_interface_name, role, ignore))
            d = AttrDict()
            d.ignore = ignore
            librenms_mgr.update_element_interface(port_id=librenms_interface.port_id, data=d)


def main():
    librenms_mgr = Librenms_Mgr(config=config.librenms)
    librenms_elements = librenms_mgr.get_elements()
    
    elements_mgr = Elements_Mgr(config=config.elements)
    tmp_api_elements = elements_mgr.get_elements()

    print("-" * 79)
    print("Checking what elements to monitor")
    api_elements = {}  # Key is hostname
    for hostname, element in tmp_api_elements.items():
        if "monitor_librenms" in element and element["monitor_librenms"] == False:
            print("  Ignoring '%s', 'monitor_librenmms' is False" % hostname)
            continue
        api_elements[hostname] = element
    print()

    print("-" * 79)
    print("Element count")
    print("  Elements API : %5d elements" % len(api_elements))
    print("  Persistent   : %5d elements" % len(config.librenms_sync.persistent_elements))
    print("  Total        : %5d elements" % (len(api_elements) + len(config.librenms_sync.persistent_elements)))
    print()
    print("  Librenms     : %5d elements" % len(librenms_elements))
    print()
    
    print("-" * 79)
    print("Update /etc/hosts file")
    utils.write_etc_hosts_file(api_elements)
    print()

    # Ok, start to compare
    
    print("-" * 79)
    print("Checking")
    
    print("  Elements that exist in Elements API but not in Librenms (action: create in librenms):")
    diff = set(api_elements.keys()) - set(librenms_elements.keys())
    if len(diff):
        for d in diff:
            print("   ", d)
            create_in_librenms.append(d)
    else:
        print("    None")
    
    print("  Element that exist in Librenms but not in elements API. (action: delete from librenms)")
    diff = set(librenms_elements.keys())
    diff = diff - set(api_elements.keys())
    diff = diff - set(config.librenms_sync.persistent_elements.keys())
    if len(diff):
        for d in diff:
            print("   ", d)
            delete_in_librenms.append(d)
    else:
        print("    None")


    # -----------------------------------------------------------------------
    
    print()
    print("-" * 79)
    print("Adjust elements in Librenms")
    print("  Creating elements in Librenms")
    if len(create_in_librenms):
        for hostname in create_in_librenms:
            print("   ", hostname)
            librenms_mgr.create_element(hostname=hostname, force_add=1)
    else:
        print("    None")
    
    print("  Deleting elements in Librenms")
    if len(delete_in_librenms):
        for hostname in delete_in_librenms:
            print("   ", hostname)
            librenms_mgr.delete_element(hostname=hostname)
    else:
        print("    None")

    print("  Updating elements in Librenms")
    for hostname, librenms_element in librenms_elements.items():
        if hostname not in api_elements:
            print("    Ignoring %s" % hostname)
            continue
        api_element = api_elements[hostname]
        if api_element:
            data = AttrDict()
             
            librenms_active = librenms_element['ignore'] == 0
            api_active = api_element['active']
            if librenms_active != api_active:
                if api_active:
                    data.ignore = 0
                else:
                    data.ignore = 1
                print("    Hostname %s, setting ignore to %s" % (hostname, data.ignore))
            
            if len(data):
                ret = librenms_mgr.update_element(hostname, data)
            if 'interfaces' in api_element:
                sync_interfaces(librenms_mgr, api_elements, hostname)
    

if __name__ == '__main__':
    try:
        main()
    except:
        # Error in script, send traceback to developer
        utils.send_traceback()
