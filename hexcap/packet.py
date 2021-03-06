#!/usr/bin/env python

'''
Copyright (c) 2014, Andrew McConachie <smutt@depht.com>
All rights reserved.
'''

import cfg
import sys
sys.path.insert(0, sys.path[0] + '/../dpkt/')
import dpkt
import layer

class Packet:
  def __init__(self, dlt, ts, packet, pid):
    self.layers = []
    self.layers.append(layer.PktID(pid))
    self.layers.append(layer.TStamp(ts))

    self.leftovers = None

    self.minSize = len(packet)
    self.maxSize = max(dpkt.ethernet.ETH_MTU, len(packet))

    # http://www.tcpdump.org/linktypes.html
    if(dlt == 1): # Ethernet
      self.initLayers(dpkt.ethernet.Ethernet(packet))
    elif(dlt == 105): # IEEE802_11
      self.initLayers(dpkt.ieee80211.IEEE80211(packet))
    elif(dlt == 192): # PPI
      self.initLayers(dpkt.ppp.PPP(packet)) # Not quite sure if this is correct
    else:
      raise PacketError("Unknown Linktype")

  # Convenience method
  # Append a new layer to this packet
  def append(self, lay):
    if(not isinstance(lay, layer.Layer)):
      raise PacketError
    else:
      self.layers.append(lay)

  # Is every layer of this packet writable
  # TODO:Add more checks in the future
  def _RW(self):
    for lay in self.layers:
      if(lay.ID == 'pid' or lay.ID == 'tstamp'):
        continue
      elif(not lay.toPcap()):
        if(not self.leftovers):
          return False
    return True
  RW = property(_RW)

  # Discover the layers in the packet and construct our layers list
  def initLayers(self, d):
    if(not isinstance(d, dpkt.Packet)):
      return

    # TODO: There has to be a better way to do this, but right now this must suffice
    if(isinstance(d, dpkt.ethernet.Ethernet)):
      if hasattr(d, 'vlan_tags'):
        if isinstance(d.vlan_tags[0], dpkt.ethernet.VLANtagISL):
          self.unsupport(d)
          return
        else:
          for tag in d.vlan_tags:
            self.layers.append(layer.Dot1q(tag))

      if d.type > 1500:
        self.layers.append(layer.EthernetII(d)) # Ethernet II
        self.initLayers(d.data)
      elif d.type == dpkt.ethernet.ETH_TYPE_IPX: # IPX
        self.unsupport(d)
        return
      else:
        self.layers.append(layer.EthernetDot3(d)) # 802.3
        self.initLayers(d.data)

    elif isinstance(d, dpkt.ieee80211.IEEE80211):
      self.layers.append(layer.Dot11(d))
      self.initLayers(d.data)

    elif isinstance(d, dpkt.llc.LLC):
      self.layers.append(layer.LLC(d))
      self.initLayers(d.data)

    elif isinstance(d, dpkt.cdp.CDP):
      self.layers.append(layer.CDP(d))
      return

    elif isinstance(d, dpkt.edp.EDP):
      self.layers.append(layer.EDP(d))
      return

    elif isinstance(d, dpkt.stp.STP):
      self.layers.append(layer.STP(d))
      self.initLayers(d.data)

    elif isinstance(d, dpkt.arp.ARP):
      self.layers.append(layer.ARP(d))
      self.initLayers(d.data)
                         
    elif isinstance(d, dpkt.ip.IP):
      self.layers.append(layer.IPv4(d))
      self.initLayers(d.data)

    elif isinstance(d, dpkt.ip6.IP6):
      self.layers.append(layer.IPv6(d))
      self.initLayers(d.data)

    elif isinstance(d, dpkt.igmp.IGMP):
      if(d.type == 0x22): # IGMPv3
        self.unsupport(d)
        return
      else:
        self.layers.append(layer.IGMP(d))
        self.initLayers(d.data)

    elif isinstance(d, dpkt.icmp.ICMP):
      self.layers.append(layer.ICMP(d))
      return

    elif isinstance(d, dpkt.tcp.TCP):
      self.layers.append(layer.TCP(d))
      self.initLayers(d.data)

    elif isinstance(d, dpkt.udp.UDP):
      self.layers.append(layer.UDP(d))
      self.initLayers(d.data)

    else:
      self.unsupport(d)
      return

  # Catchall function for unsupported protocols
  # If we find an unsupported protocol we end up here
  # Just save the leftovers in one generic layer
  def unsupport(self, d):
    self.layers.append(layer.Leftovers(d))
  
  # Sets the value of section,column to val
  def setColumn(self, sid, col, val):
    for lay in self.layers:
      if(lay.ID == sid):
        lay.setColumn(col, val)

  # Transforms a packet into a sleep statement
  def makeSleep(self, seconds):
    self.layers = self.layers[0:2]
    self.layers.append(layer.Control('s', seconds))
    self.layer('tstamp').vals['tstamp'] = ''

  # Transforms a packet into a jump statement
  def makeJump(self, jmpPid):
    self.layers = self.layers[0:2]
    self.layers.append(layer.Control('j', jmpPid))
    self.layer('tstamp').vals['tstamp'] = ''

  # Adds a generator to a layer
  def addGenerator(self, sid, cid, count, step):
    for lay in self.layers:
      if(lay.ID == sid):
        rv =  lay.addGenerator(cid, count, step)
        if(rv):
          return rv
        else:
          if(not self.control):
            self.layer('tstamp').vals['tstamp'] = ''
            self.layers.insert(1, layer.Control('g'))

  # Adds a mask to a layer
  def addMask(self, sid, cid, mask):
    for lay in self.layers:
      if(lay.ID == sid):
        lay.addMask(cid, mask)
        if(not self.control):
          self.layer('tstamp').vals['tstamp'] = ''
          self.layers.insert(1, layer.Control('g'))
        break

  # Returns list of all layers with generators
  # Returns False if packet has no generators
  def _get_genLayers(self):
    if(self.control == 'g'):
      rv = []
      for lay in self.layers:
        if(hasattr(lay, 'gen')):
          rv.append(lay)
      return rv
    return False
  genLayers = property(_get_genLayers)

  # Returns the type of control statement this packet is; 'g', 's', OR 'j'
  # If it's a normal packet returns false
  def _get_control(self):
    for lay in self.layers:
      if(isinstance(lay, layer.Control)):
        return lay.vals['c'].strip()
    return False
  control = property(_get_control)

  # Returns the first layer with layID
  def layer(self, layID):
    for lay in self.layers:
      if(lay.ID == layID):
        return lay

  # Convenience method
  # Returns PID of packet
  def getPID(self):
    for lay in self.layers:
      if(lay.ID == 'pid'):
        return lay.vals['pid']

  # Convenience method
  # Returns timestamp of packet
  def getTS(self):
    for lay in self.layers:
      if(isinstance(TStamp, lay)):
        return lay.vals['tstamp']

  # Convenience method
  # Return True if passed sid corresponds with existing layer in pkt
  # Else returns False
  def hasLayer(self, sid):
    for lay in self.layers:
      if(lay.ID == sid):
        return True
    return False

  # Returns the dpkt packet object
  # Does not work with timestamps
  # Returns False if pcap data cannot be constructed
  def data(self):
    for lay in self.layers:
      if(lay.ID == 'pid' or lay.ID == 'tstamp' or lay.ID == 'cntrl'):
        continue
      elif(isinstance(lay, layer.Ethernet)):
        rv = lay.toPcap()
      else:
        rv = self.pushDpktLayer(rv, lay.toPcap())

    return self.sizePkt(rv)

  # Totally unpythonic but don't care
  # Takes a dpktObj and pushes a new layer onto it
  # We're basically just treating the dpkt object like a stack
  def pushDpktLayer(self, dpktObj, lay):
    d = dpktObj
    while(isinstance(d.data, dpkt.Packet)):
      d = d.data
    d.data = lay
    return dpktObj

  # Pads our pcap packet and returns False on packets greater than MTU
  def sizePkt(self, pkt):
    if((len(pkt) >= self.minSize) & (len(pkt) <= self.maxSize)):
      return pkt
    elif(len(pkt) > self.maxSize):
      raise PacketError, "Packet to write larger than MTU"
    else:
      d = pkt
      while(isinstance(d.data, dpkt.Packet)):
        d = d.data
      for ii in xrange(len(pkt), self.minSize):
        d.data += '\x00'
    return pkt

  # For debugging only
  def __repr__(self):
    rv = ''
    for lay in self.layers:
      rv += "\n" + lay.ID + repr(lay)
    return rv

  def __str__(self):
    return self.__repr__()

  def out(self):
    rv = dict()
    for lay in self.layers:
      rv[lay.ID] = lay.vals
    return rv

class PacketError(Exception):
  pass
