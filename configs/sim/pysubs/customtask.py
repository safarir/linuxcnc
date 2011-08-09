import os

import emctask
import emccanon
import interpreter
import hal
import tooltable

try:
    import cPickle as pickle
except ImportError:
    import pickle

try:
    from userfuncs import UserFuncs
except ImportError:
    from nulluserfuncs import UserFuncs

def debug():
    return interpreter.this.debugmask &  0x00040000 # EMC_DEBUG_PYTHON_TASK


class CustomTask(emctask.Task,UserFuncs):
    def __init__(self):
        emctask.Task.__init__(self)

        h = hal.component("iocontrol.0")
        h.newpin("coolant-flood", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("coolant-mist", hal.HAL_BIT, hal.HAL_OUT)

        h.newpin("lube-level", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("lube", hal.HAL_BIT, hal.HAL_OUT)

        h.newpin("emc-enable-in", hal.HAL_BIT, hal.HAL_IN)
        h.newpin("user-enable-out", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("user-request-enable", hal.HAL_BIT, hal.HAL_OUT)

        h.newpin("tool-change", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("tool-changed", hal.HAL_BIT, hal.HAL_IN)
        h.newpin("tool-number", hal.HAL_S32, hal.HAL_OUT)
        h.newpin("tool-prep-number", hal.HAL_S32, hal.HAL_OUT)
        h.newpin("tool-prep-pocket", hal.HAL_S32, hal.HAL_OUT)
        h.newpin("tool-prepare", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin("tool-prepared", hal.HAL_BIT, hal.HAL_IN)
        h.ready()
        self.components = dict()
        self.components["iocontrol.0"] = h
        self.hal = h
        self.hal_init_pins()
        self.e = emctask.emcstat
        self.e.io.aux.estop = 1
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        self._callback = None
        self._check = None
        UserFuncs.__init__(self)
        self.enqueue = EnqueueCall(self)
        print "Py CustomTask.init"

    def emcIoInit(self):
        print "py:  emcIoInit tt=",self.tooltable_filename
        self.tt = tooltable.EmcToolTable(self.tooltable_filename, self.random_toolchanger)
        self.comments = dict()
        self.tt.load(self.e.io.tool.toolTable,self.comments)

        # on nonrandom machines, always start by assuming the spindle is empty
        if not self.random_toolchanger:
             self.e.io.tool.toolTable[0].zero()

        self.e.io.aux.estop = 1
        self.e.io.tool.pocketPrepped = -1;
        self.e.io.tool.toolInSpindle = 0;
        self.e.io.coolant.mist = 0
        self.e.io.coolant.flood = 0
        self.e.io.lube.on = 0
        self.e.io.lube.level = 1

        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcToolLoadToolTable(self,file):
        print "py:  emcToolLoadToolTable file =",file
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def prepare_complete(self):
        print "prepare complete"
        self.e.io.tool.pocketPrepped = self.hal["tool-prep-pocket"]
        self.hal["tool-prepare"] = 0
        pass

    def emcToolPrepare(self,p,tool):
        print "py:   emcToolPrepare p =",p,"tool =",tool
        if self.random_toolchanger and (p == 0):
            print "it doesn't make sense to prep the spindle pocket"
            return 0

        self.hal["tool-prep-pocket"] = p
        if not self.random_toolchanger and (p == 0):
            self.hal["tool-prep-number"] = 0
        else:
            self.hal["tool-prep-number"] = self.e.io.tool.toolTable[p].toolno

        self.hal["tool-prepare"] = 1
        # and tell task to wait until status changes to RCS_DONE
        self.e.io.status =  self.wait_for_named_pin(1,"iocontrol.0.tool-prepared",self.prepare_complete)
        return 0

    def load_tool(self,pocket):
        if self.random_toolchanger:
            self.e.io.tool.toolTable[0],self.e.io.tool.toolTable[pocket] = self.e.io.tool.toolTable[pocket],self.e.io.tool.toolTable[0]
            self.comments[0],self.comments[pocket] = self.comments[pocket],self.comments[0]
            self.tt.save(self.e.io.tool.toolTable,self.comments)
        else:
            if pocket == 0:
                self.e.io.tool.toolTable[0].zero()
            else:
                # self.e.io.tool.toolTable[0].assign(self.e.io.tool.toolTable[pocket])
                self.e.io.tool.toolTable[0] = self.e.io.tool.toolTable[pocket]

    def change_complete(self):
        print "change complete"
        if not self.random_toolchanger and (self.e.io.tool.pocketPrepped == 0):
            self.e.io.tool.toolInSpindle = 0
        else:
            self.e.io.tool.toolInSpindle = self.e.io.tool.toolTable[self.e.io.tool.pocketPrepped].toolno
        self.hal["tool-number"] = self.e.io.tool.toolInSpindle
        self.load_tool(self.e.io.tool.pocketPrepped)
        self.e.io.tool.pocketPrepped = -1
        self.hal["tool-prep-number"] = 0
        self.hal["tool-prep-pocket"] = 0
        self.hal["tool-change"] = 0
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcToolLoad(self):
        print "py:  emcToolLoad"

        if self.random_toolchanger and (self.e.io.tool.pocketPrepped == 0):
            self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
            return 0

        if not self.random_toolchanger and (self.e.io.tool.pocketPrepped > 0) and self.e.io.tool.toolInSpindle == self.e.io.tool.toolTable[self.e.io.tool.pocketPrepped].toolno:
            self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
            return 0

        if self.e.io.tool.pocketPrepped != -1:
            self.hal["tool-change"] = 1
            self.e.io.status =  self.wait_for_named_pin(1,"iocontrol.0.tool-changed",self.change_complete)
            return 0
        return 0

    def emcToolUnload(self):
        print "py:  emcToolUnload"
        self.e.io.tool.toolInSpindle = 0
        # this isnt in ioControlv1, but I think it should be.
        self.hal["tool-number"] = self.e.io.tool.toolInSpindle
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcToolSetNumber(self,number):
        print "py:   emcToolSetNumber number =",number
        self.e.io.tool.toolInSpindle = number
        self.hal["tool-number"] = self.e.io.tool.toolInSpindle
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcToolSetOffset(self,pocket,toolno,offset,diameter,frontangle,backangle,orientation):
        print "py:  emcToolSetOffset", pocket,toolno,str(offset),diameter,frontangle,backangle,orientation

        self.e.io.tool.toolTable[pocket].toolno = toolno
        self.e.io.tool.toolTable[pocket].orientation = orientation
        self.e.io.tool.toolTable[pocket].diameter = diameter
        self.e.io.tool.toolTable[pocket].frontangle = frontangle
        self.e.io.tool.toolTable[pocket].backangle = backangle
        self.e.io.tool.toolTable[pocket].offset = offset

        if debug(): print "new tool enttry: ",str(self.e.io.tool.toolTable[pocket])

        if self.e.io.tool.toolInSpindle  == toolno:
            self.e.io.tool.toolTable[0] = self.e.io.tool.toolTable[pocket]


        self.tt.save(self.e.io.tool.toolTable,self.comments)
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0


    def emcIoPluginCall(self, len, msg):
        print "py: emcIoPluginCall" # ,msg
        call = pickle.loads(msg)
        func = getattr(self, call[0], None)
        if func:
            self.e.io.status = func(*call[1],**call[2])
        else:
            raise AttributeError, "no such method: " + call[0]
        return 0


    def emcIoHalt(self):
        print "py:  emcIoHalt"
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcIoAbort(self,reason):
        print "py:  emcIoAbort reason=",reason,"state=",self.e.task.state
        self.hal["coolant-mist"] = 0
        self.e.io.coolant.mist = 0
        self.hal["coolant-flood"] = 0
        self.e.io.coolant.flood = 0
        self.hal["tool-change"] = 0
        self.hal["tool-prepare"] = 0
        if self._callback:
            print "emcIoAbort: cancelling callback to ",self._callback
            self._callback = None
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcToolStartChange(self):
        print "py:  emcToolStartChange NIY"
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcAuxEstopOn(self):
        print "py:  emcAuxEstopOn taskstate=",self.e.task.state
        self.hal["user-enable-out"] = 0
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcAuxEstopOff(self):
        print "py:  emcAuxEstopOff"
        self.hal["user-enable-out"] = 1
        self.hal["user-request-enable"] = 1
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcCoolantMistOn(self):
        print "py:  emcCoolantMistOn"
        self.hal["coolant-mist"] = 1
        self.e.io.coolant.mist = 1
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcCoolantMistOff(self):
        print "py:  emcCoolantMistOff"
        self.hal["coolant-mist"] = 0
        self.e.io.coolant.mist = 0
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcCoolantFloodOn(self):
        print "py:  emcCoolantFloodOn"
        self.hal["coolant-flood"] = 1
        self.e.io.coolant.flood = 1
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcCoolantFloodOff(self):
        print "py:  emcCoolantFloodOff"
        self.hal["coolant-flood"] = 0
        self.e.io.coolant.flood = 0
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcLubeOn(self):
        print "py:  emcLubeOn"
        self.hal["lube"] = 1
        self.e.io.lube.on = 1
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcLubeOff(self):
        print "py:  emcLubeOff"
        self.hal["lube"] = 0
        self.e.io.lube.on = 0
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcIoSetDebug(self,debug):
        print "py:   emcIoSetDebug debug =",debug
        self.e.io.status  = emctask.RCS_STATUS.RCS_DONE
        return 0

    def emcIoUpdate(self):
        #        print "py:  emcIoUpdate"
        self.hal["user-request-enable"] = 0
        self.e.io.aux.estop = not self.hal["emc-enable-in"]
        if self._check:
            self.e.io.status  = self._check()
        return 0

    def wait_for_named_pin_callback(self):
        if self._comp[self._pin] == self._value:
            print "wait CB done"
            if self._callback: self._callback()
            self._check = None
            self._callback = None
            return emctask.RCS_STATUS.RCS_DONE
        return emctask.RCS_STATUS.RCS_EXEC

    def wait_for_named_pin(self,value,name,callback = None):
        print "wait_for_named_pin ",value,name
        (component, pin) = name.rsplit('.',1)
        comp = self.components[component]
        if comp[pin] == value:
            print "wait_for_named_pin: already set"
            if callback: callback()
            return emctask.RCS_STATUS.RCS_DONE
        # else set up callback
        self._comp = comp
        self._pin = pin
        self._value = value
        self._check = self.wait_for_named_pin_callback
        self._callback = callback
        # and tell task to wait until status changes to RCS_DONE
        return emctask.RCS_STATUS.RCS_EXEC


    def hal_init_pins(self):
        """ Sets HAL pins default values """
        self.hal["user-enable-out"] = 0
        self.hal["user-request-enable"] = 0
        self.hal["coolant-mist"] = 0
        self.hal["coolant-flood"] = 0
        self.hal["lube"] = 0
        self.hal["tool-prepare"] = 0
        self.hal["tool-prepared"] = 0
        self.hal["tool-prep-number"] = 0
        self.hal["tool-prep-pocket"] = 0
        self.hal["tool-change"] = 0
        self.hal["tool-number"] = 0



# support queuing calls from Interp to Task Python methods:
# trap call, pickle a tuple of name and arguments and enqueue with canon IO_PLUGIN_CALL
class EnqueueCall(object):
    def __init__(self,e):
        print "EnqueueCall.__init__()"
        self._e = e

    def _encode(self,*args,**kwargs):
        if hasattr(self._e,self._name) and callable(getattr(self._e,self._name)):
            p = pickle.dumps((self._name,args,kwargs)) # ,-1) # hm, binary wont work just yet
            emccanon.IO_PLUGIN_CALL(int(len(p)),p)
        else:
            raise AttributeError,"no such Task method: " + self._name

    def __getattr__(self, name):
        self._name = name
        return self._encode
