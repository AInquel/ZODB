#! /usr/bin/env python
##############################################################################
#
# Copyright (c) 2001, 2002 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.0 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE
#
##############################################################################
"""Cache simulation.

Usage: simul.py [-bflyz] [-s size] tracefile

Use one of -b, -f, -l, -y or -z select the cache simulator:
-b: buddy system allocator
-f: simple free list allocator
-l: idealized LRU (no allocator)
-y: variation on the existing ZEO cache that copies to current file
-z: existing ZEO cache (default)

Options:
-s size: cache size in MB (default 20 MB)

Note: the buddy system allocator rounds the cache size up to a power of 2
"""

import sys
import time
import getopt
import struct
import math

from sets import Set

def usage(msg):
    print >>sys.stderr, msg
    print >>sys.stderr, __doc__

def main():
    # Parse options
    MB = 1000*1000
    cachelimit = 20*MB
    simclass = ZEOCacheSimulation
    theta = omicron = None
    try:
        opts, args = getopt.getopt(sys.argv[1:], "bflyz2cOaTUs:o:t:")
    except getopt.error, msg:
        usage(msg)
        return 2
    for o, a in opts:
        if o == '-b':
            simclass = BuddyCacheSimulation
        if o == '-f':
            simclass = SimpleCacheSimulation
        if o == '-l':
            simclass = LRUCacheSimulation
        if o == '-y':
            simclass = AltZEOCacheSimulation
        if o == '-z':
            simclass = ZEOCacheSimulation
        if o == '-s':
            cachelimit = int(float(a)*MB)
        if o == '-2':
            simclass = TwoQSimluation
        if o == '-c':
            simclass = CircularCacheSimulation
        if o == '-o':
            omicron = float(a)
        if o == '-t':
            theta = float(a)
        if o == '-O':
            simclass = OracleSimulation
        if o == '-a':
            simclass = ARCCacheSimulation
        if o == '-T':
            simclass = ThorSimulation
        if o == '-U':
            simclass = UnboundedSimulation

    if len(args) != 1:
        usage("exactly one file argument required")
        return 2
    filename = args[0]

    if omicron is not None and simclass != CircularCacheSimulation:
        usage("-o flag only useful with -c (CircularCacheSimulation)")
        return 2

    # Open file
    if filename.endswith(".gz"):
        # Open gzipped file
        try:
            import gzip
        except ImportError:
            print >>sys.stderr,  "can't read gzipped files (no module gzip)"
            return 1
        try:
            f = gzip.open(filename, "rb")
        except IOError, msg:
            print >>sys.stderr,  "can't open %s: %s" % (filename, msg)
            return 1
    elif filename == "-":
        # Read from stdin
        f = sys.stdin
    else:
        # Open regular file
        try:
            f = open(filename, "rb")
        except IOError, msg:
            print >>sys.stderr,  "can't open %s: %s" % (filename, msg)
            return 1

    # Create simulation object
    if omicron is not None or theta is not None:
        sim = simclass(cachelimit, omicron, theta)
    elif simclass is OracleSimulation:
        sim = simclass(cachelimit, filename)
    else:
        sim = simclass(cachelimit)

    # Print output header
    sim.printheader()

    # Read trace file, simulating cache behavior
    offset = 0
    records = 0
    f_read = f.read
    struct_unpack = struct.unpack
    while 1:
        # Read a record and decode it
        r = f_read(8)
        if len(r) < 8:
            break
        offset += 8
        ts, code = struct_unpack(">ii", r)
        if ts == 0:
            # Must be a misaligned record caused by a crash
            ##print "Skipping 8 bytes at offset", offset-8
            continue
        r = f_read(16)
        if len(r) < 16:
            break
        offset += 16
        records += 1
        oid, serial = struct_unpack(">8s8s", r)
        # Decode the code
        dlen, version, code, current = (code & 0x7fffff00,
                                        code & 0x80,
                                        code & 0x7e,
                                        code & 0x01)
        # And pass it to the simulation
        sim.event(ts, dlen, version, code, current, oid, serial)

    # Finish simulation
    sim.finish()

    # Exit code from main()
    return 0

class Simulation:

    """Base class for simulations.

    The driver program calls: event(), printheader(), finish().

    The standard event() method calls these additional methods:
    write(), load(), inval(), report(), restart(); the standard
    finish() method also calls report().

    """

    def __init__(self, cachelimit):
        self.cachelimit = cachelimit
        # Initialize global statistics
        self.epoch = None
        self.total_loads = 0
        self.total_hits = 0       # subclass must increment
        self.total_invals = 0     # subclass must increment
        self.total_writes = 0
        if not hasattr(self, "extras"):
            self.extras = (self.extraname,)
        self.format = self.format + " %7s" * len(self.extras)
        # Reset per-run statistics and set up simulation data
        self.restart()

    def restart(self):
        # Reset per-run statistics
        self.loads = 0
        self.hits = 0       # subclass must increment
        self.invals = 0     # subclass must increment
        self.writes = 0
        self.ts0 = None

    def event(self, ts, dlen, _version, code, _current, oid, _serial):
        # Record first and last timestamp seen
        if self.ts0 is None:
            self.ts0 = ts
            if self.epoch is None:
                self.epoch = ts
        self.ts1 = ts

        # Simulate cache behavior.  Use load hits, updates and stores
        # only (each load miss is followed immediately by a store
        # unless the object in fact did not exist).  Updates always write.
        if dlen and code & 0x70 in (0x20, 0x30, 0x50):
            if code == 0x3A:
                # Update
                self.writes += 1
                self.total_writes += 1
                self.write(oid, dlen)
            else:
                # Load hit or store -- these are really the load requests
                self.loads += 1
                self.total_loads += 1
                self.load(oid, dlen)
        elif code & 0x70 == 0x10:
            # Invalidate
            self.inval(oid)
        elif code == 0x00:
            # Restart
            self.report()
            self.restart()

    def write(self, oid, size):
        pass

    def load(self, oid, size):
        # Must increment .hits and .total_hits as appropriate.
        pass

    def inval(self, oid):
        # Must increment .invals and .total_invals as appropriate.
        pass

    format = "%12s %9s %8s %8s %6s %6s %7s"

    # Subclass should override extraname to name known instance variables;
    # if extraname is 'foo', both self.foo and self.total_foo must exist:
    extraname = "*** please override ***"

    def printheader(self):
        print "%s, cache size %s bytes" % (self.__class__.__name__,
                                           addcommas(self.cachelimit))
        self.extraheader()
        extranames = tuple([s.upper() for s in self.extras])
        args = ("START TIME", "DURATION", "LOADS", "HITS",
                "INVALS", "WRITES", "HITRATE") + extranames
        print self.format % args

    def extraheader(self):
        pass

    nreports = 0

    def report(self, extratext=''):
        if self.loads:
            self.nreports += 1
            args = (time.ctime(self.ts0)[4:-8],
                    duration(self.ts1 - self.ts0),
                    self.loads, self.hits, self.invals, self.writes,
                    hitrate(self.loads, self.hits))
            args += tuple([getattr(self, name) for name in self.extras])
            print self.format % args, extratext

    def finish(self):
        # Make sure that the last line of output ends with "OVERALL".  This
        # makes it much easier for another program parsing the output to
        # find summary statistics.
        if self.nreports < 2:
            self.report('OVERALL')
        else:
            self.report()
            args = (
                time.ctime(self.epoch)[4:-8],
                duration(self.ts1 - self.epoch),
                self.total_loads,
                self.total_hits,
                self.total_invals,
                self.total_writes,
                hitrate(self.total_loads, self.total_hits))
            args += tuple([getattr(self, "total_" + name)
                           for name in self.extras])
            print (self.format + " OVERALL") % args

class ZEOCacheSimulation(Simulation):

    """Simulate the current (ZEO 1.0 and 2.0) ZEO cache behavior.

    This assumes the cache is not persistent (we don't know how to
    simulate cache validation.)

    """

    extraname = "flips"

    def __init__(self, cachelimit):
        # Initialize base class
        Simulation.__init__(self, cachelimit)
        # Initialize additional global statistics
        self.total_flips = 0

    def restart(self):
        # Reset base class
        Simulation.restart(self)
        # Reset additional per-run statistics
        self.flips = 0
        # Set up simulation
        self.filesize = [4, 4] # account for magic number
        self.fileoids = [{}, {}]
        self.current = 0 # index into filesize, fileoids

    def load(self, oid, size):
        if (self.fileoids[self.current].get(oid) or
            self.fileoids[1 - self.current].get(oid)):
            self.hits += 1
            self.total_hits += 1
        else:
            self.write(oid, size)

    def write(self, oid, size):
        # Fudge because size is rounded up to multiples of 256.  (31
        # is header overhead per cache record; 127 is to compensate
        # for rounding up to multiples of 256.)
        size = size + 31 - 127
        if self.filesize[self.current] + size > self.cachelimit / 2:
            # Cache flip
            self.flips += 1
            self.total_flips += 1
            self.current = 1 - self.current
            self.filesize[self.current] = 4
            self.fileoids[self.current] = {}
        self.filesize[self.current] += size
        self.fileoids[self.current][oid] = 1

    def inval(self, oid):
        if self.fileoids[self.current].get(oid):
            self.invals += 1
            self.total_invals += 1
            del self.fileoids[self.current][oid]
        elif self.fileoids[1 - self.current].get(oid):
            self.invals += 1
            self.total_invals += 1
            del self.fileoids[1 - self.current][oid]

class AltZEOCacheSimulation(ZEOCacheSimulation):

    """A variation of the ZEO cache that copies to the current file.

    When a hit is found in the non-current cache file, it is copied to
    the current cache file.  Exception: when the copy would cause a
    cache flip, we don't copy (this is part laziness, part concern
    over causing extraneous flips).
    """

    def load(self, oid, size):
        if self.fileoids[self.current].get(oid):
            self.hits += 1
            self.total_hits += 1
        elif self.fileoids[1 - self.current].get(oid):
            self.hits += 1
            self.total_hits += 1
            # Simulate a write, unless it would cause a flip
            size = size + 31 - 127
            if self.filesize[self.current] + size <= self.cachelimit / 2:
                self.filesize[self.current] += size
                self.fileoids[self.current][oid] = 1
                del self.fileoids[1 - self.current][oid]
        else:
            self.write(oid, size)

class LRUCacheSimulation(Simulation):

    extraname = "evicts"

    def __init__(self, cachelimit):
        # Initialize base class
        Simulation.__init__(self, cachelimit)
        # Initialize additional global statistics
        self.total_evicts = 0

    def restart(self):
        # Reset base class
        Simulation.restart(self)
        # Reset additional per-run statistics
        self.evicts = 0
        # Set up simulation
        self.cache = {}
        self.size = 0
        self.head = Node(None, None)
        self.head.linkbefore(self.head)

    def load(self, oid, size):
        node = self.cache.get(oid)
        if node is not None:
            self.hits += 1
            self.total_hits += 1
            node.linkbefore(self.head)
        else:
            self.write(oid, size)

    def write(self, oid, size):
        node = self.cache.get(oid)
        if node is not None:
            node.unlink()
            assert self.head.next is not None
            self.size -= node.size
        node = Node(oid, size)
        self.cache[oid] = node
        node.linkbefore(self.head)
        self.size += size
        # Evict LRU nodes
        while self.size > self.cachelimit:
            self.evicts += 1
            self.total_evicts += 1
            node = self.head.next
            assert node is not self.head
            node.unlink()
            assert self.head.next is not None
            del self.cache[node.oid]
            self.size -= node.size

    def inval(self, oid):
        node = self.cache.get(oid)
        if node is not None:
            assert node.oid == oid
            self.invals += 1
            self.total_invals += 1
            node.unlink()
            assert self.head.next is not None
            del self.cache[oid]
            self.size -= node.size
            assert self.size >= 0

class Node:

    """Node in a doubly-linked list, storing oid and size as payload.

    A node can be linked or unlinked; in the latter case, next and
    prev are None.  Initially a node is unlinked.

    """
    # Make it a new-style class in Python 2.2 and up; no effect in 2.1
    __metaclass__ = type
    __slots__ = ['prev', 'next', 'oid', 'size']

    def __init__(self, oid, size):
        self.oid = oid
        self.size = size
        self.prev = self.next = None

    def unlink(self):
        prev = self.prev
        next = self.next
        if prev is not None:
            assert next is not None
            assert prev.next is self
            assert next.prev is self
            prev.next = next
            next.prev = prev
            self.prev = self.next = None
        else:
            assert next is None

    def linkbefore(self, next):
        self.unlink()
        prev = next.prev
        if prev is None:
            assert next.next is None
            prev = next
        self.prev = prev
        self.next = next
        prev.next = next.prev = self

am = object()
a1in = object()
a1out = object()

class Node2Q(Node):

    __slots__ = ["kind", "hits"]

    def __init__(self, oid, size, kind=None):
        Node.__init__(self, oid, size)
        self.kind = kind
        self.hits = 0

    def linkbefore(self, next):
        if next.kind != self.kind:
            self.kind = next.kind
        Node.linkbefore(self, next)

class TwoQSimluation(Simulation):

    # The original 2Q algorithm is page based and the authors offer
    # tuning guidlines based on a page-based cache.  Our cache is
    # object based, so, for example, it's hard to compute the number
    # of oids to store in a1out based on the size of a1in.

    extras = "evicts", "hothit", "am_add"

    NodeClass = Node2Q

    def __init__(self, cachelimit, outlen=10000, threshold=0):
        Simulation.__init__(self, cachelimit)

        # The promotion threshold: If a hit occurs in a1out, it is
        # promoted to am if the number of hits on the object while it
        # was in a1in is at least threshold.  The standard 2Q scheme
        # uses a threshold of 0.
        self.threshold = threshold
        self.am_limit = 3 * self.cachelimit / 4
        self.a1in_limit = self.cachelimit / 4

        self.cache = {}
        self.am_size = 0
        self.a1in_size = 0
        self.a1out_size = 0

        self.total_evicts = 0
        self.total_hothit = 0
        self.total_am_add = 0
        self.a1out_limit = outlen

        # An LRU queue of hot objects
        self.am = self.NodeClass(None, None, am)
        self.am.linkbefore(self.am)
        # A FIFO queue of recently referenced objects.  It's purpose
        # is to absorb references to objects that are accessed a few
        # times in short order, then forgotten about.
        self.a1in = self.NodeClass(None, None, a1in)
        self.a1in.linkbefore(self.a1in)
        # A FIFO queue of recently reference oids.
        # This queue only stores the oids, not any data.  If we get a
        # hit in this queue, promote the object to am.
        self.a1out = self.NodeClass(None, None, a1out)
        self.a1out.linkbefore(self.a1out)

    def makespace(self, size):
        for space in 0, size:
            if self.enoughspace(size):
                return
            self.evict_a1in(space)
            if self.enoughspace(size):
                return
            self.evict_am(space)

    def enoughspace(self, size):
        totalsize = self.a1in_size + self.am_size
        return totalsize + size < self.cachelimit

    def evict_a1in(self, extra):
        while self.a1in_size + extra > self.a1in_limit:
            if self.a1in.next is self.a1in:
                return
            assert self.a1in.next is not None
            node = self.a1in.next
            self.evicts += 1
            self.total_evicts += 1
            node.linkbefore(self.a1out)
            self.a1out_size += 1
            self.a1in_size -= node.size
            if self.a1out_size > self.a1out_limit:
                assert self.a1out.next is not None
                node = self.a1out.next
                node.unlink()
                del self.cache[node.oid]
                self.a1out_size -= 1

    def evict_am(self, extra):
        while self.am_size + extra > self.am_limit:
            if self.am.next is self.am:
                return
            assert self.am.next is not None
            node = self.am.next
            self.evicts += 1
            self.total_evicts += 1
            # This node hasn't been accessed in a while, so just
            # forget about it.
            node.unlink()
            del self.cache[node.oid]
            self.am_size -= node.size

    def write(self, oid, size):
        # A write always follows a read (ZODB doesn't allow blind writes).
        # So this write must have followed a recent read of the object.
        # Don't change it's position, but do update the size.

        # XXX For now, don't evict pages if the new version of the object
        # is big enough to require eviction.
        node = self.cache.get(oid)
        if node is None or node.kind is a1out:
            return
        if node.kind is am:
            self.am_size = self.am_size - node.size + size
            node.size = size
        else:
            self.a1in_size = self.a1in_size - node.size + size
            node.size = size

    def load(self, oid, size):
        node = self.cache.get(oid)
        if node is not None:
            if node.kind is am:
                self.hits += 1
                self.total_hits += 1
                self.hothit += 1
                self.total_hothit += 1
                node.hits += 1
                node.linkbefore(self.am)
            elif node.kind is a1in:
                self.hits += 1
                self.total_hits += 1
                node.hits += 1
            elif node.kind is a1out:
                self.a1out_size -= 1
                if node.hits >= self.threshold:
                    self.makespace(node.size)
                    self.am_size += node.size
                    node.linkbefore(self.am)
                    self.cache[oid] = node
                    self.am_add += 1
                    self.total_am_add += 1
                else:
                    node.unlink()
                    self.insert(oid, size)
        else:
            self.insert(oid, size)

    def insert(self, oid, size):
        # New objects enter the cache via a1in.  If they
        # are frequently used over a long enough time, they
        # will be promoted to am -- but only via a1out.
        self.makespace(size)
        node = self.NodeClass(oid, size, a1in)
        node.linkbefore(self.a1in)
        self.cache[oid] = node
        self.a1in_size += node.size

    def inval(self, oid):
        # The original 2Q algorithm didn't have to deal with
        # invalidations.  My own solution: Move it to the head of
        # a1out.
        node = self.cache.get(oid)
        if node is None:
            return
        self.invals += 1
        self.total_invals += 1
        # XXX Should an invalidation to a1out count?
        if node.kind is a1out:
            return
        node.linkbefore(self.a1out)
        if node.kind is am:
            self.am_size -= node.size
        else:
            self.a1in_size -= node.size

    def restart(self):
        Simulation.restart(self)

        self.evicts = 0
        self.hothit = 0
        self.am_add = 0

lruT = object()
lruB = object()
fifoT = object()
fifoB = object()

class ARCCacheSimulation(Simulation):

    # Based on the paper ARC: A Self-Tuning, Low Overhead Replacement
    # Cache by Nimrod Megiddo and Dharmendra S. Modha, USENIX FAST
    # 2003.  The paper describes a block-based cache.  A lot of the
    # details need to be fiddled to work with an object-based cache.
    # For size issues, the key insight ended up being conditions
    # A.1-A.5 rather than the details of the algorithm in Fig. 4.

    extras = "lruThits", "evicts", "p"

    def __init__(self, cachelimit):
        Simulation.__init__(self, cachelimit)
        # There are two pairs of linked lists.  Each pair has a top and
        # bottom half.  The bottom half contains metadata, but not actual
        # objects.

        # LRU list of frequently used objects
        self.lruT = Node2Q(None, None, lruT)
        self.lruT.linkbefore(self.lruT)
        self.lruT_len = 0
        self.lruT_size = 0

        self.lruB = Node2Q(None, None, lruB)
        self.lruB.linkbefore(self.lruB)
        self.lruB_len = 0
        self.lruB_size = 0

        # FIFO list of objects seen once
        self.fifoT = Node2Q(None, None, fifoT)
        self.fifoT.linkbefore(self.fifoT)
        self.fifoT_len = 0
        self.fifoT_size = 0

        self.fifoB = Node2Q(None, None, fifoB)
        self.fifoB.linkbefore(self.fifoB)
        self.fifoB_len = 0
        self.fifoB_size = 0

        # maps oid to node
        self.cache = {}

        # The paper says that p should be adjust be 1 as the minimum:
        # "The compound effect of such small increments and decrements
        # to p s quite profound as we will demonstrated in the next
        # section."  Not really, as far as I can tell.  In my traces
        # with a very small cache, it was taking far too long to adjust
        # towards favoring some FIFO component.  I would guess that the
        # chief difference is that our caches are much bigger than the
        # ones they experimented with.  Their biggest cache had 512K
        # entries, while our smallest cache will have 40 times that many
        # entries.

        self.p = 0
        # XXX multiply computed adjustments to p by walk_factor
        self.walk_factor = 500

        # statistics
        self.total_hits = 0
        self.total_lruThits = 0
        self.total_fifoThits = 0
        self.total_evicts = 0

    def restart(self):
        Simulation.restart(self)
        self.hits = 0
        self.lruThits = 0
        self.fifoThits = 0
        self.evicts = 0

    def write(self, oid, size):
        pass

    def replace(self, lruB=False):
        self.evicts += 1
        self.total_evicts += 1
        if self.fifoT_size > self.p or (lruB and self.fifoT_size == self.p):
            node = self.fifoT.next
            if node is self.fifoT:
                return 0
            assert node is not self.fifoT, self.stats()
            node.linkbefore(self.fifoB)
            self.fifoT_len -= 1
            self.fifoT_size -= node.size
            self.fifoB_len += 1
            self.fifoB_size += node.size
        else:
            node = self.lruT.next
            if node is self.lruT:
                return 0
            assert node is not self.lruT, self.stats()
            node.linkbefore(self.lruB)
            self.lruT_len -= 1
            self.lruT_size -= node.size
            self.lruB_len += 1
            self.lruB_size += node.size
        return node.size

    def stats(self):
        self.totalsize = self.lruT_size + self.fifoT_size
        self.allsize = self.totalsize + self.lruB_size + self.fifoB_size
        print "cachelimit = %s totalsize=%s allsize=%s" % (
            addcommas(self.cachelimit),
            addcommas(self.totalsize),
            addcommas(self.allsize))

        fmt = (
            "p=%(p)d\n"
            "lruT  = %(lruT_len)5d / %(lruT_size)8d / %(lruThits)d\n"
            "fifoT = %(fifoT_len)5d / %(fifoT_size)8d / %(fifoThits)d\n"
            "lruB  = %(lruB_len)5d / %(lruB_size)8d\n"
            "fifoB = %(fifoB_len)5d / %(fifoB_size)8d\n"
            "loads=%(loads)d hits=%(hits)d evicts=%(evicts)d\n"
            )
        print fmt % self.__dict__

    def report(self):
        self.total_p = self.p
        Simulation.report(self)
##        self.stats()

    def load(self, oid, size):
##        maybe(self.stats, p=0.002)
        node = self.cache.get(oid)
        if node is None:
            # cache miss: We're going to insert a new object in fifoT.
            # If fifo is full, we'll need to evict something to make
            # room for it.

            prev = need = size
            while need > 0:
                if size + self.fifoT_size + self.fifoB_size >= self.cachelimit:
                    if need + self.fifoT_size >= self.cachelimit:
                        node = self.fifoB.next
                        assert node is not self.fifoB, self.stats()
                        node.unlink()
                        del self.cache[node.oid]
                        self.fifoB_size -= node.size
                        self.fifoB_len -= 1
                        self.evicts += 1
                        self.total_evicts += 1
                    else:
                        node = self.fifoB.next
                        assert node is not self.fifoB, self.stats()
                        node.unlink()
                        del self.cache[node.oid]
                        self.fifoB_size -= node.size
                        self.fifoB_len -= 1
                        if self.fifoT_size + self.lruT_size > self.cachelimit:
                            need -= self.replace()
                else:
                    incache_size = self.fifoT_size + self.lruT_size + need
                    total_size = (incache_size + self.fifoB_size
                                  + self.lruB_size)
                    if total_size >= self.cachelimit * 2:
                        node = self.lruB.next
                        if node is self.lruB:
                            break
                        assert node is not self.lruB
                        node.unlink()
                        del self.cache[node.oid]
                        self.lruB_size -= node.size
                        self.lruB_len -= 1
                    elif incache_size > self.cachelimit:
                        need -= self.replace()
                    else:
                        break
                if need == prev:
                    # XXX hack, apparently we can't get rid of anything else
                    break
                prev = need

            node = Node2Q(oid, size)
            node.linkbefore(self.fifoT)
            self.fifoT_len += 1
            self.fifoT_size += size
            self.cache[oid] = node
        else:
            # a cache hit, but possibly in a bottom list that doesn't
            # actually hold the object
            if node.kind is lruT:
                node.linkbefore(self.lruT)

                self.hits += 1
                self.total_hits += 1
                self.lruThits += 1
                self.total_lruThits += 1

            elif node.kind is fifoT:
                node.linkbefore(self.lruT)
                self.fifoT_len -= 1
                self.lruT_len += 1
                self.fifoT_size -= node.size
                self.lruT_size += node.size

                self.hits += 1
                self.total_hits += 1
                self.fifoThits += 1
                self.total_fifoThits += 1

            elif node.kind is fifoB:
                node.linkbefore(self.lruT)
                self.fifoB_len -= 1
                self.lruT_len += 1
                self.fifoB_size -= node.size
                self.lruT_size += node.size

                # XXX need a better min than 1?
##                print "adapt+", max(1, self.lruB_size // self.fifoB_size)
                delta = max(1, self.lruB_size / max(1, self.fifoB_size))
                self.p += delta * self.walk_factor
                if self.p > self.cachelimit:
                    self.p = self.cachelimit

                need = node.size
                if self.lruT_size + self.fifoT_size + need > self.cachelimit:
                    while need > 0:
                        r = self.replace()
                        if not r:
                            break
                        need -= r

            elif node.kind is lruB:
                node.linkbefore(self.lruT)
                self.lruB_len -= 1
                self.lruT_len += 1
                self.lruB_size -= node.size
                self.lruT_size += node.size

                # XXX need a better min than 1?
##                print "adapt-", max(1, self.fifoB_size // self.lruB_size)
                delta = max(1, self.fifoB_size / max(1, self.lruB_size))
                self.p -= delta * self.walk_factor
                if self.p < 0:
                    self.p = 0

                need = node.size
                if self.lruT_size + self.fifoT_size + need > self.cachelimit:
                    while need > 0:
                        r = self.replace(lruB=True)
                        if not r:
                            break
                        need -= r

    def inval(self, oid):
        pass

    def extraheader(self):
        pass

class OracleSimulation(LRUCacheSimulation):

    # Not sure how to implement this yet.  This is a cache where I
    # cheat to see how good we could actually do.  The cache
    # replacement problem for multi-size caches is NP-hard, so we're
    # not going to have an optimal solution.

    # At the moment, the oracle is mostly blind.  It knows which
    # objects will be referenced more than once, so that it can
    # ignore objects referenced only once.  In most traces, these
    # objects account for about 20% of references.

    def __init__(self, cachelimit, filename):
        LRUCacheSimulation.__init__(self, cachelimit)
        self.count = {}
        self.scan(filename)

    def load(self, oid, size):
        node = self.cache.get(oid)
        if node is not None:
            self.hits += 1
            self.total_hits += 1
            node.linkbefore(self.head)
        else:
            if oid in self.count:
                self.write(oid, size)

    def scan(self, filename):
        # scan the file in advance to figure out which objects will
        # be referenced more than once.
        f = open(filename, "rb")
        struct_unpack = struct.unpack
        f_read = f.read
        offset = 0
        while 1:
            # Read a record and decode it
            r = f_read(8)
            if len(r) < 8:
                break
            offset += 8
            ts, code = struct_unpack(">ii", r)
            if ts == 0:
                # Must be a misaligned record caused by a crash
                ##print "Skipping 8 bytes at offset", offset-8
                continue
            r = f_read(16)
            if len(r) < 16:
                break
            offset += 16
            oid, serial = struct_unpack(">8s8s", r)
            if code & 0x70 == 0x20:
                # only look at loads
                self.count[oid] = self.count.get(oid, 0) + 1

        all = len(self.count)

        # Now remove everything with count == 1
        once = [oid for oid, count in self.count.iteritems()
                if count == 1]
        for oid in once:
            del self.count[oid]

        print "Scanned file, %d unique oids, %d repeats" % (
            all, len(self.count))

class CircularCacheSimulation(Simulation):

    # The cache is managed as a single file with a pointer that
    # goes around the file, circularly, forever.  New objects
    # are written at the current pointer, evicting whatever was
    # there previously.

    # For each cache hit, there is some distance between the current
    # pointer offset and the offset of the cached data record.  The
    # cache can be configured to copy objects to the current offset
    # depending on how far away they are now.  The omicron parameter
    # specifies a percentage

    extras = "evicts", "copies", "inuse", "skips"

    def __init__(self, cachelimit, omicron=None, skip=None):
        Simulation.__init__(self, cachelimit)
        self.omicron = omicron or 0
        self.skip = skip or 0
        self.total_evicts = 0
        self.total_copies = 0
        self.total_skips = 0
        # Current offset in file
        self.offset = 0
        # Map offset in file to tuple of size, oid
        self.filemap = {0: (self.cachelimit, None)}
        # Map oid to offset, node
        self.cache = {}
        # LRU list of oids
        self.head = Node(None, None)
        self.head.linkbefore(self.head)

    def extraheader(self):
        print "omicron = %s, theta = %s" % (self.omicron, self.skip)

    def restart(self):
        Simulation.restart(self)
        self.evicts = 0
        self.copies = 0
        self.skips = 0

    def load(self, oid, size):
        p = self.cache.get(oid)
        if p is None:
            self.add(oid, size)
        else:
            pos, node = p
            self.hits += 1
            self.total_hits += 1
            node.linkbefore(self.head)
            self.copy(oid, size, pos)

    def check(self):
        d = dict(self.filemap)
        done = {}
        while d:
            pos, (size, oid) = d.popitem()
            next = pos + size
            if not (next in d or next in done or next == self.cachelimit):
                print "check", next, pos, size, repr(oid)
                self.dump()
                raise RuntimeError
            done[pos] = pos

    def dump(self):
        print len(self.filemap)
        L = list(self.filemap)
        L.sort()
        for k in L:
            v = self.filemap[k]
            print k, v[0], repr(v[1])

    def add(self, oid, size):
        avail = self.makeroom(size)
        assert oid not in self.cache
        self.filemap[self.offset] = size, oid
        node = Node(oid, size)
        node.linkbefore(self.head)
        self.cache[oid] = self.offset, node
        self.offset += size
        # All the space made available must be accounted for in filemap.
        excess = avail - size
        if excess:
            self.filemap[self.offset] = excess, None

    def makeroom(self, need):
        if self.offset + need > self.cachelimit:
            self.offset = 0
        pos = self.offset
        # Evict enough objects to make the necessary space available.
        self.compute_closeenough()
        evicted = False
        while need > 0:
            if pos == self.cachelimit:
                print "wrap makeroom", need
                pos = 0
            try:
                size, oid = self.filemap[pos]
            except:
                self.dump()
                raise

            if not evicted and self.skip and oid and self.closeenough(oid):
                self.skips += 1
                self.total_skips += 1
                self.offset += size
                pos += size
                continue

            evicted = True
            del self.filemap[pos]

            if oid is not None:
                self.evicts += 1
                self.total_evicts += 1
                pos, node = self.cache.pop(oid)
                node.unlink()
            need -= size
            pos += size

        return pos - self.offset

    def compute_closeenough(self):
        self.lru = {}
        n = int(len(self.cache) * self.skip)
        node = self.head.prev
        while n > 0:
            self.lru[node.oid] = True
            node = node.prev
            n -= 1

    def closeenough(self, oid):
        # If oid is in the top portion of the most recently used
        # elements, skip it.
        return oid in self.lru

    def copy(self, oid, size, pos):
        # Copy only if the distance is greater than omicron.
        dist = self.offset - pos
        if dist < 0:
            dist += self.cachelimit
        if dist < self.omicron * self.cachelimit:
            self.copies += 1
            self.total_copies += 1
            self.filemap[pos] = size, None
            pos, node =  self.cache.pop(oid)
            node.unlink()
            self.add(oid, size)

    def inval(self, oid):
        p = self.cache.get(oid)
        if p is None:
            return
        pos, node = p
        self.invals += 1
        self.total_invals += 1
        size, _oid = self.filemap[pos]
        assert oid == _oid
        self.filemap[pos] = size, None
        pos, node = self.cache.pop(oid)
        node.unlink()

    def write(self, oid, size):
        p = self.cache.get(oid)
        if p is None:
            return
        pos, node = p
        oldsize, _oid = self.filemap[pos]
        assert oid == _oid
        if size == oldsize:
            return
        if size < oldsize:
            excess = oldsize - size
            self.filemap[pos] = size, oid
            self.filemap[pos + size] = excess, None
        else:
            self.filemap[pos] = oldsize, None
            pos, node = self.cache.pop(oid)
            node.unlink()
            self.add(oid, size)

    def report(self):
        free = used = total = 0
        for size, oid in self.filemap.itervalues():
            total += size
            if oid:
                used += size
            else:
                free += size

        self.inuse = round(100.0 * used / total, 1)
        self.total_inuse = self.inuse
        Simulation.report(self)

class BuddyCacheSimulation(LRUCacheSimulation):

    def __init__(self, cachelimit):
        LRUCacheSimulation.__init__(self, roundup(cachelimit))

    def restart(self):
        LRUCacheSimulation.restart(self)
        self.allocator = self.allocatorFactory(self.cachelimit)

    def allocatorFactory(self, size):
        return BuddyAllocator(size)

    # LRUCacheSimulation.load() is just fine

    def write(self, oid, size):
        node = self.cache.get(oid)
        if node is not None:
            node.unlink()
            assert self.head.next is not None
            self.size -= node.size
            self.allocator.free(node)
        while 1:
            node = self.allocator.alloc(size)
            if node is not None:
                break
            # Failure to allocate.  Evict something and try again.
            node = self.head.next
            assert node is not self.head
            self.evicts += 1
            self.total_evicts += 1
            node.unlink()
            assert self.head.next is not None
            del self.cache[node.oid]
            self.size -= node.size
            self.allocator.free(node)
        node.oid = oid
        self.cache[oid] = node
        node.linkbefore(self.head)
        self.size += node.size

    def inval(self, oid):
        node = self.cache.get(oid)
        if node is not None:
            assert node.oid == oid
            self.invals += 1
            self.total_invals += 1
            node.unlink()
            assert self.head.next is not None
            del self.cache[oid]
            self.size -= node.size
            assert self.size >= 0
            self.allocator.free(node)

class SimpleCacheSimulation(BuddyCacheSimulation):

    def allocatorFactory(self, size):
        return SimpleAllocator(size)

    def finish(self):
        BuddyCacheSimulation.finish(self)
        self.allocator.report()

MINSIZE = 256

class BuddyAllocator:

    def __init__(self, cachelimit):
        cachelimit = roundup(cachelimit)
        self.cachelimit = cachelimit
        self.avail = {} # Map rounded-up sizes to free list node heads
        self.nodes = {} # Map address to node
        k = MINSIZE
        while k <= cachelimit:
            self.avail[k] = n = Node(None, None) # Not BlockNode; has no addr
            n.linkbefore(n)
            k += k
        node = BlockNode(None, cachelimit, 0)
        self.nodes[0] = node
        node.linkbefore(self.avail[cachelimit])

    def alloc(self, size):
        size = roundup(size)
        k = size
        while k <= self.cachelimit:
            head = self.avail[k]
            node = head.next
            if node is not head:
                break
            k += k
        else:
            return None # Store is full, or block is too large
        node.unlink()
        size2 = node.size
        while size2 > size:
            size2 = size2 / 2
            assert size2 >= size
            node.size = size2
            buddy = BlockNode(None, size2, node.addr + size2)
            self.nodes[buddy.addr] = buddy
            buddy.linkbefore(self.avail[size2])
        node.oid = 1 # Flag as in-use
        return node

    def free(self, node):
        assert node is self.nodes[node.addr]
        assert node.prev is node.next is None
        node.oid = None # Flag as free
        while node.size < self.cachelimit:
            buddy_addr = node.addr ^ node.size
            buddy = self.nodes[buddy_addr]
            assert buddy.addr == buddy_addr
            if buddy.oid is not None or buddy.size != node.size:
                break
            # Merge node with buddy
            buddy.unlink()
            if buddy.addr < node.addr: # buddy prevails
                del self.nodes[node.addr]
                node = buddy
            else: # node prevails
                del self.nodes[buddy.addr]
            node.size *= 2
        assert node is self.nodes[node.addr]
        node.linkbefore(self.avail[node.size])

    def dump(self, msg=""):
        if msg:
            print msg,
        size = MINSIZE
        blocks = bytes = 0
        while size <= self.cachelimit:
            head = self.avail[size]
            node = head.next
            count = 0
            while node is not head:
                count += 1
                node = node.next
            if count:
                print "%d:%d" % (size, count),
            blocks += count
            bytes += count*size
            size += size
        print "-- %d, %d" % (bytes, blocks)

def roundup(size):
    k = MINSIZE
    while k < size:
        k += k
    return k

class SimpleAllocator:

    def __init__(self, arenasize):
        self.arenasize = arenasize
        self.avail = BlockNode(None, 0, 0) # Weird: empty block as list head
        self.rover = self.avail
        node = BlockNode(None, arenasize, 0)
        node.linkbefore(self.avail)
        self.taglo = {0: node}
        self.taghi = {arenasize: node}
        # Allocator statistics
        self.nallocs = 0
        self.nfrees = 0
        self.allocloops = 0
        self.freebytes = arenasize
        self.freeblocks = 1
        self.allocbytes = 0
        self.allocblocks = 0

    def report(self):
        print ("NA=%d AL=%d NF=%d ABy=%d ABl=%d FBy=%d FBl=%d" %
               (self.nallocs, self.allocloops,
                self.nfrees,
                self.allocbytes, self.allocblocks,
                self.freebytes, self.freeblocks))

    def alloc(self, size):
        self.nallocs += 1
        # First fit algorithm
        rover = stop = self.rover
        while 1:
            self.allocloops += 1
            if rover.size >= size:
                break
            rover = rover.next
            if rover is stop:
                return None # We went round the list without finding space
        if rover.size == size:
            self.rover = rover.next
            rover.unlink()
            del self.taglo[rover.addr]
            del self.taghi[rover.addr + size]
            self.freeblocks -= 1
            self.allocblocks += 1
            self.freebytes -= size
            self.allocbytes += size
            return rover
        # Take space from the beginning of the roving pointer
        assert rover.size > size
        node = BlockNode(None, size, rover.addr)
        del self.taglo[rover.addr]
        rover.size -= size
        rover.addr += size
        self.taglo[rover.addr] = rover
        #self.freeblocks += 0 # No change here
        self.allocblocks += 1
        self.freebytes -= size
        self.allocbytes += size
        return node

    def free(self, node):
        self.nfrees += 1
        self.freeblocks += 1
        self.allocblocks -= 1
        self.freebytes += node.size
        self.allocbytes -= node.size
        node.linkbefore(self.avail)
        self.taglo[node.addr] = node
        self.taghi[node.addr + node.size] = node
        x = self.taghi.get(node.addr)
        if x is not None:
            # Merge x into node
            x.unlink()
            self.freeblocks -= 1
            del self.taglo[x.addr]
            del self.taghi[x.addr + x.size]
            del self.taglo[node.addr]
            node.addr = x.addr
            node.size += x.size
            self.taglo[node.addr] = node
        x = self.taglo.get(node.addr + node.size)
        if x is not None:
            # Merge x into node
            x.unlink()
            self.freeblocks -= 1
            del self.taglo[x.addr]
            del self.taghi[x.addr + x.size]
            del self.taghi[node.addr + node.size]
            node.size += x.size
            self.taghi[node.addr + node.size] = node
        # It's possible that either one of the merges above invalidated
        # the rover.
        # It's simplest to simply reset the rover to the newly freed block.
        self.rover = node

    def dump(self, msg=""):
        if msg:
            print msg,
        count = 0
        bytes = 0
        node = self.avail.next
        while node is not self.avail:
            bytes += node.size
            count += 1
            node = node.next
        print count, "free blocks,", bytes, "free bytes"
        self.report()

class BlockNode(Node):

    __slots__ = ['addr']

    def __init__(self, oid, size, addr):
        Node.__init__(self, oid, size)
        self.addr = addr

def testallocator(factory=BuddyAllocator):
    # Run one of Knuth's experiments as a test
    import random
    import heapq # This only runs with Python 2.3, folks :-)
    reportfreq = 100
    cachelimit = 2**17
    cache = factory(cachelimit)
    queue = []
    T = 0
    blocks = 0
    while T < 5000:
        while queue and queue[0][0] <= T:
            time, node = heapq.heappop(queue)
            assert time == T
            ##print "free addr=%d, size=%d" % (node.addr, node.size)
            cache.free(node)
            blocks -= 1
        size = random.randint(100, 2000)
        lifetime = random.randint(1, 100)
        node = cache.alloc(size)
        if node is None:
            print "out of mem"
            cache.dump("T=%4d: %d blocks;" % (T, blocks))
            break
        else:
            ##print "alloc addr=%d, size=%d" % (node.addr, node.size)
            blocks += 1
            heapq.heappush(queue, (T + lifetime, node))
        T = T+1
        if T % reportfreq == 0:
            cache.dump("T=%4d: %d blocks;" % (T, blocks))

def hitrate(loads, hits):
    return "%5.1f%%" % (100.0 * hits / max(1, loads))

def duration(secs):

    mm, ss = divmod(secs, 60)
    hh, mm = divmod(mm, 60)
    if hh:
        return "%d:%02d:%02d" % (hh, mm, ss)
    if mm:
        return "%d:%02d" % (mm, ss)
    return "%d" % ss

def addcommas(n):
    sign, s = '', str(n)
    if s[0] == '-':
        sign, s = '-', s[1:]
    i = len(s) - 3
    while i > 0:
        s = s[:i] + ',' + s[i:]
        i -= 3
    return sign + s

import random

def maybe(f, p=0.5):
    if random.random() < p:
        f()

#############################################################################
# Thor-like eviction scheme.
#
# The cache keeps such a list of all objects, and uses a travelling pointer
# to decay the worth of objects over time.

class ThorNode(Node):

    __slots__ = ['worth']

    def __init__(self, oid, size, worth=None):
        Node.__init__(self, oid, size)
        self.worth = worth

class ThorListHead(Node):
    def __init__(self):
        Node.__init__(self, 0, 0)
        self.next = self.prev = self

class ThorSimulation(Simulation):

    extras = "evicts", "trips"

    def __init__(self, cachelimit):
        Simulation.__init__(self, cachelimit)

        # Maximum total of object sizes we keep in cache.
        self.maxsize = cachelimit
        # Current total of object sizes in cache.
        self.currentsize = 0

        # A worth byte maps to a set of all objects with that worth.
        # This is cheap to keep updated, and makes finding low-worth
        # objects for eviction trivial (just march over the worthsets
        # list, in order).
        self.worthsets = [Set() for dummy in range(256)]

        # We keep a circular list of all objects in cache.  currentobj
        # walks around it forever.  Each time _tick() is called, the
        # worth of currentobj is decreased, basically by shifting
        # right 1, and currentobj moves on to the next object.  When
        # an object is first inserted, it enters the list right before
        # currentobj.  When an object is accessed, its worth is
        # increased by or'ing in 0x80.  This scheme comes from the
        # Thor system, and is an inexpensive way to account for both
        # recency and frequency of access:  recency is reflected in
        # the leftmost bit set, and frequency by how many bits are
        # set.
        #
        # Note:  because evictions are interleaved with ticks,
        # unlinking an object is tricky, lest we evict currentobj.  The
        # class _unlink method takes care of this properly.
        self.listhead = ThorListHead()
        self.currentobj = self.listhead

        # Map an object.oid to its ThorNode.
        self.oid2object = {}

        self.total_evicts = self.total_trips = 0

    # Unlink object from the circular list, taking care not to lose
    # track of the current object.  Always call this instead of
    # invoking obj.unlink() directly.
    def _unlink(self, obj):
        assert obj is not self.listhead
        if obj is self.currentobj:
            self.currentobj = obj.next
        obj.unlink()

    # Change obj.worth to newworth, maintaining invariants.
    def _change_worth(self, obj, newworth):
        if obj.worth != newworth:
            self.worthsets[obj.worth].remove(obj)
            obj.worth = newworth
            self.worthsets[newworth].add(obj)

    def add(self, object):
        assert object.oid not in self.oid2object
        self.oid2object[object.oid] = object

        newsize = self.currentsize + object.size
        if newsize > self.maxsize:
            self._evictbytes(newsize - self.maxsize)
        self.currentsize += object.size
        object.linkbefore(self.currentobj)

        if object.worth is None:
            # Give smaller objects higher initial worth.  This favors kicking
            # out unreferenced large objects before kicking out unreferenced
            # small objects.  On real life traces, this is a significant
            # win for the hit rate.
            object.worth = 32 - int(round(math.log(object.size, 2)))
        self.worthsets[object.worth].add(object)

    # Decrease the worth of the current object, and advance the
    # current object.
    def _tick(self):
        c = self.currentobj
        if c is self.listhead:
            c = c.next
            if c is self.listhead:  # list is empty
                return
            self.total_trips += 1
            self.trips += 1
        self._change_worth(c, (c.worth + 1) >> 1)
        self.currentobj = c.next

    def access(self, oid):
        self._tick()
        obj = self.oid2object.get(oid)
        if obj is None:
            return None
        self._change_worth(obj, obj.worth | 0x80)
        return obj

    # Evict objects of least worth first, until at least nbytes bytes
    # have been freed.
    def _evictbytes(self, nbytes):
        for s in self.worthsets:
            while s:
                if nbytes <= 0:
                    return
                obj = s.pop()
                nbytes -= obj.size
                self._evictobj(obj)

    def _evictobj(self, obj):
        self.currentsize -= obj.size
        self.worthsets[obj.worth].discard(obj)
        del self.oid2object[obj.oid]
        self._unlink(obj)

        self.evicts += 1
        self.total_evicts += 1

    def _evict_without_bumping_evict_stats(self, obj):
        self._evictobj(obj)
        self.evicts -= 1
        self.total_evicts -= 1

    # Simulator overrides from here on.

    def restart(self):
        # Reset base class
        Simulation.restart(self)
        # Reset additional per-run statistics
        self.evicts = self.trips = 0

    def write(self, oid, size):
        obj = self.oid2object.get(oid)
        worth = None
        if obj is not None:
            worth = obj.worth
            self._evict_without_bumping_evict_stats(obj)
        self.add(ThorNode(oid, size, worth))

    def load(self, oid, size):
        if self.access(oid) is not None:
            self.hits += 1
            self.total_hits += 1
        else:
            self.write(oid, size)

    def inval(self, oid):
        obj = self.oid2object.get(oid)
        if obj is not None:
            self.invals += 1
            self.total_invals += 1
            self._evict_without_bumping_evict_stats(obj)

    # Take the "x" off to see additional stats after each restart period.
    def xreport(self):
        Simulation.report(self)
        print 'non-empty worth sets', sum(map(bool, self.worthsets)),
        print 'objects', len(self.oid2object),
        print 'size', self.currentsize

#############################################################################
# Perfection:  What if the cache were unbounded, and never forgot anything?
# This simulator answers that question directly; the cache size parameter
# isn't used.

class UnboundedSimulation(Simulation):

    extraname = 'evicts'   # for some reason we *have* to define >= 1 extra

    def __init__(self, cachelimit):
        Simulation.__init__(self, cachelimit)
        self.oids = Set()
        self.evicts = self.total_evicts = 0

    def write(self, oid, size):
        self.oids.add(oid)

    def load(self, oid, size):
        if oid in self.oids:
            self.hits += 1
            self.total_hits += 1
        else:
            self.oids.add(oid)

    def inval(self, oid):
        if oid in self.oids:
            self.invals += 1
            self.total_invals += 1
            self.oids.remove(oid)

if __name__ == "__main__":
    sys.exit(main())
