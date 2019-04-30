"""
The ``pins`` module provides an API for tracking, discovering and sharing datasets.
"""

import os
import yaml
from _pins_cffi import ffi
import subprocess
import platform
import sys

def _get_rhome():
    r_home = os.environ.get("R_HOME")
    if r_home:
        return r_home
    tmp = subprocess.check_output(("R", "RHOME"), universal_newlines=True)
    r_home = tmp.split(os.linesep)
    if r_home[0].startswith("WARNING"):
        r_home = r_home[1]
    else:
        r_home = r_home[0].strip()
    return r_home

def _get_rlib():
    r_home = _get_rhome()
    system = platform.system()
    if system == "Linux":
        lib_path = os.path.join(r_home, "lib", "libR.so")
    elif system == "Darwin":
        lib_path = os.path.join(r_home, "lib", "libR.dylib")
    else:
        raise ValueError("System '%s' is unsupported.")
    return lib_path

def _open_rlib():
    return ffi.dlopen(_get_rlib())

def _print(message):
    sys.stdout.write(message)
    sys.stdout.flush()

@ffi.callback("void(char *, int, int)")
def _console_write(buffer, size, otype):
    _print(ffi.string(buffer, size).decode("utf-8"))

@ffi.callback("void(char *)")
def _showmessage(buffer):
    _print(ffi.string(buffer).decode("utf-8"))

@ffi.callback('void(SA_TYPE, int, int)')
def _cleanup(saveact, status, runlast):
    pass

@ffi.callback('void(void)')
def _processevents():
    pass

@ffi.callback('void(int)')
def _busy(which):
    pass
    
def _main_loop_started():
    return rlib.ptr_R_WriteConsoleEx != ffi.NULL or rlib.R_GlobalEnv != ffi.NULL

rlib = None
def r_start():
    global rlib
    if (rlib != None):
        return rlib
        
    os.environ["R_HOME"] = _get_rhome()
    rlib = _open_rlib()

    if (_main_loop_started()):
        return rlib
        
    import atexit
    atexit.register(r_end, 0)
        
    options = ("pins", "--quiet", "--vanilla", "--no-save")
    options_raw = [ffi.new("char[]", o.encode("ASCII")) for o in options]
    status = rlib.Rf_initialize_R(ffi.cast("int", len(options_raw)), options_raw)

    rlib.ptr_R_WriteConsoleEx = _console_write
    rlib.ptr_R_WriteConsole = ffi.NULL
    rlib.ptr_R_CleanUp = _cleanup
    rlib.ptr_R_ProcessEvents = _processevents
    rlib.ptr_R_Busy = _busy

    rlib.setup_Rmainloop()

    return rlib

def r_end(fatal):
    rlib.R_dot_Last()
    rlib.R_RunExitFinalizers()
    rlib.Rf_KillAllDevices()
    rlib.R_CleanTempDir()
    rlib.R_gc()
    rlib.Rf_endEmbeddedR(fatal)

def r_eval(code, environment = None):
    r_start()
    
    cmdSexp = rlib.Rf_allocVector(rlib.STRSXP, 1)
    rlib.Rf_protect(cmdSexp)
    rlib.SET_STRING_ELT(cmdSexp, 0, rlib.Rf_mkChar(code));
    
    status = ffi.new("ParseStatus *")
    cmdexpr = rlib.Rf_protect(rlib.R_ParseVector(cmdSexp, -1, status, rlib.R_NilValue))

    rlib.Rf_unprotect(2)
    if status[0] != rlib.PARSE_OK:
        raise RuntimeError("Failed to parse: " + code)

    if environment == None:
        environment = rlib.R_GlobalEnv
        
    error = ffi.new("int *")

    result = rlib.Rf_protect(rlib.R_tryEval(rlib.VECTOR_ELT(cmdexpr, 0), environment, error))

    if (error[0]):
        message = r_eval("gsub('\\\n', '', geterrmessage())")
        raise RuntimeError(message + " at " + code)

    rtype = result.sxpinfo.type
    if (rtype == rlib.CHARSXP):
        result = ffi.string(rlib.R_CHAR(result))
    elif (rtype == rlib.STRSXP):
        result = ffi.string(rlib.R_CHAR(rlib.STRING_ELT(result, 0)))
    elif (rtype == rlib.RAWSXP):
        n = rlib.Rf_xlength(result)
        result = ffi.buffer(rlib.RAW(result), n)

    rlib.Rf_unprotect(1)
    return result

def _init_pins():
    r_start()
    r_eval("""
        if (!"pins" %in% installed.packages()) {
            if (!"remotes" %in% installed.packages())
                install.packages("remotes")

            remotes::install_github("rstudio/pins")
        }
    """)
    r_eval("library('pins')")

def find_pin(text = ""):
    """
    Find Pin.
    """
    _init_pins()
    return r_eval("pins::as_arrow(pins::find_pin(\"" + text + "\"))")

def get_pin(name, board = None):
    """
    Retrieve Pin.
    """
    _init_pins()
    buffer = r_eval("pins::as_arrow(pins::get_pin(\"" + name + "\"))")
    
    import pyarrow as pa
    return pa.ipc.open_stream(buffer).read_pandas()

def pin(x, name, description = "", board = None):
    """
    Create Pin.
    """
    _init_pins()
