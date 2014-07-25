#!/usr/bin/env python
#
#   This script can be used to document many of the connected interfaces on
#   the switche automatically.  It will do the following:
#   1) Label interfaces where CDP neighbors are found
#   2) Label port-channel interfaces based on where the CDP of member ports
#   3) Label ports and port-channels connected to Fabric Extenders (Nexus 2000s)
#
#   The basic methodology for gathering the data was borrowed from: 
#   https://github.com/datacenter/nexus7000/blob/master/cdp_description.py
#

from cisco import *
import xml.etree.cElementTree as ET


def short_name(str):
  ''' 
  This function shortens the interface name for easier reading 
  '''
  if 'TenGigabitEthernet' in str:
    return str.replace('GigabitEthernet', 'T')
  elif 'GigabitEthernet' in str:
    return str.replace('GigabitEthernet', 'G') 
  elif 'FastEthernet' in str:
    return str.replace('FastEthernet', 'F')
  elif 'Ethernet' in str: 
    return str.replace('Ethernet', 'e')
  elif 'Eth' in str: 
    return str.replace('Eth', 'e')
  elif 'port-channel' in str:
    return str.replace('port-channel', 'Po')
  else:
    return str


def ncli(command, xml=False):
  '''
  This function gets the cli output for a supplied command.  This function 
  accounts for the difference between 7K and 5K outputs.  7Ks return a 
  string from the cli() command, while the 5K returns a tuple and we have 
  to extract the second element to get the string.
  '''
  if xml:
    raw = cli(command + ' | xml | exclude "]]>]]>"')
  else:
    raw = cli(command)
  #5Ks return cli() as tuple, so extract string if type is a tuple
  if type(raw) == tuple:
    return raw[1]
  else:
    return raw


def get_element(command):
  '''
  Fetches and parses the XML output of the supplied command into an
  ElementTree object.  It also returns the base key, so we only need
  to type the unique portion of each key when extracting data.
  '''
  #Get raw XML output from command
  raw = ncli(command, xml=True)
  #Parse raw data into an element tree  
  tree = ET.ElementTree(ET.fromstring(raw))
  root = tree.getroot()
  #Get base string for keys.  Should look something like "{http://www.cisco.com/nxos:1.0:cdpd}"
  base = str(root[0][0]).split("'")[1]
  while base[-1] != '}':
    base = base[:-1]
  #Return root element and base for keys
  return root, base


def make_cdp_dict():
  '''
  This function create a dictionary of CDP information from the XML output of
  the "show cdp neighbors" command.  The local interface is the key, and the
  value is a tuple with the remote hostname and remote interface.
  '''
  cdp_dict = {}
  #Get tree (data) and the base string for the elements
  data, cdp_base = get_element('show cdp neighbors')
  #Strip off end characters until string end with closing curly brace
  for i in data.iter(cdp_base + 'ROW_cdp_neighbor_brief_info'):
    #parse interface, remote device ID and port from xml output
    l_port = i.find(cdp_base + 'intf_id').text
    r_name = i.find(cdp_base + 'device_id').text
    #Remove Serial number from name by splitting string at "(" and taking the first element
    r_name = r_name.split('(')[0]
    #Remove Domain suffix by splitting at "." and taking the first element
    r_name = r_name.split('.')[0]
    r_port = i.find(cdp_base + 'port_id').text
    #save the info in a dictionary
    cdp_dict[l_port] = (r_name, r_port)
  return cdp_dict


def make_pc_dict():
  '''
  This function pulls the port-channel information and creates a dictionary
  with port-channel name being the key and a list of members as the value.
  '''
  pc_dict = {}
  data, pc_base = get_element('show port-channel summary')
  for pc in data.iter(pc_base + 'ROW_channel'):
    pc_name = pc.find(pc_base + 'port-channel').text
    if pc_name not in pc_dict:
      pc_dict[pc_name] = []
    for member in pc.iter(pc_base + 'ROW_member'):
      pc_dict[pc_name].append(member.find(pc_base + 'port').text)
  return pc_dict


def make_fex_dict():
  ''' 
  This function creates a dictionary based on FEX informations.  The 
  key in this dictionary is the FEX number, and the value is a list of
  interfaces that are connected to the FEX.
  '''
  #Verify platform has FEX enabled before trying to build this dictionary
  fex_enabled = False
  platform = ncli('show version | i Nexus')
    #5Ks return cli() as tuple, so extract string if type is a tuple
  if 'Nexus 7' in platform:
    if "enable" in ncli('show feature-set | i fex'):
      fex_enabled = True
  elif 'Nexus 5' in platform:
    if "enable" in ncli('show feature | i fex'):
      fex_enabled = True
  fex_dict={}
  if fex_enabled:    
    data, fex_base = get_element('show fex detail')
    for fex in data.iter(fex_base + 'ROW_fex_info'):
      fex_id = fex.find(fex_base + 'chas_id').text
      if fex_id not in fex_dict:
        fex_dict[fex_id] = []
      for intf in fex.iter(fex_base + 'ROW_fbr_state'):
        fex_dict[fex_id].append(intf.find(fex_base + 'fbr_index').text)
  return fex_dict


def make_desc_dict(cdp_dict, pc_dict, fex_dict):
  '''
  This function merges the information from the CDP, Port-channel and
  FEX dictionaries into a single dictionary that will contain all of
  the information that will be used to create port labels.
  '''
  #Copy CDP into merged dictionary as a starting point
  merge = cdp_dict
  for pc, plist in pc_dict.iteritems():
    if (len(plist) > 0 and merge.has_key(plist[0])):
      merge[pc] = (merge[plist[0]], None)
  for num, portlist in fex_dict.iteritems():
    for port in portlist:
      merge[port] = ('FEX' + num, None)
  return merge


def write_desc(int_dict):
  '''
  This function takes the description dictionary as an input and
  parses it into a list of commands that will be pushed to the 
  configuration.  First it lets the user review the commands before
  waiting for input on whether to write to the configuration or not.
  '''
  command_list = []
  for intf, remote in sorted(int_dict.iteritems()):
    if remote[1] == None:
      command_list.append("int %s ; description %s" % (short_name(intf), remote[0]))
    else:
      command_list.append("int %s ; description %s:%s" % (short_name(intf), remote[0], short_name(remote[1])))
  print "This will add the following commands to the configuration: "
  for line in command_list:
    print "conf ; " + line
  print ("Continue? (y/n) ")
  response = raw_input()
  if (response.lower() == "yes" or response.lower() == "y"):
    for line in command_list:
      cli("conf ; " + line)
    print "*** DON'T forget to save the configuration!"
  else:
    print "Skipped writing to the configuration"

def main():
  write_desc(make_desc_dict(make_cdp_dict(), make_pc_dict(), make_fex_dict()))

if __name__ == '__main__':
    main()
