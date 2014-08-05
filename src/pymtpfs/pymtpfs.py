'''
@author: Donald Munro
License: Apache V2 (http://www.apache.org/licenses/LICENSE-2.0.txt)

Usage: pymtpfs.py Version 0.0.2
Interpreter:  2.6 <= Python < 3.0 
pymtpfs.py [-vDNLes] [device] mountpoint (If device not specified first available device is mounted)
pymtpfs.py -l (List available devices)

Options:
  -h, --help            show this help message and exit
  -v, --verbose         Verbose
  -D, --debug           Debug mode
  -N, --nolog           No logging whatsoever (not even errors to syslog)
  -L LOG, --log=LOG     Logging options. Argument format is 
                        filename,maxsize-in-Mb,count                       
                        Filename can be an
                        absolute path in which case the directory of the file
                        is used for log files eg
                        /root/logs/pymtpfs/pymtpfs.log or it can be relative
                        to the current directory eg
                        logs/pymtpfs.log. If there is no directory in the path
                        then the current directory is used for logging.
                        Default for maxsize is 10Mb
                        Default for count is 1                       
                        eg -L logs/pymtpfs.log,8,5 
                        results in up to 5 log files named pymtpfs.log.[0-4]
                        with a max size of 8Mb per file
  -e LOGLEVEL, --loglevel=LOGLEVEL
                        Log Level. One of ['DEBUG', 'INFO', 'WARNING', 'ERROR'] 
                        eg -e DEBUG -l, 
                        --list            List available MTP devices and exit

'''

import sys
import os
import stat 
import errno
import signal
import time
import tempfile
import logging
import logging.handlers
import copy
import shutil
import traceback
from optparse import OptionParser
from collections import namedtuple
from functools import wraps
try:
   from fuse import FUSE, Operations, LoggingMixIn, FuseOSError
except ImportError:
   sys.stderr.write("""Requires fusepy - Simple ctypes bindings for FUSE 
   (https://github.com/terencehonles/fusepy , https://github.com/terencehonles/fusepy)
   To install with pip: pip install fusepy
   """)
   sys.exit(1)
   
from lru import LRU
from mtp import MTP

VERSION = "0.0.2"
STOPPED = DEBUG = VERBOSE = False
LOGGER = None
LOG_LEVELS = { 'DEBUG' : logging.DEBUG, 'INFO' : logging.INFO, 'WARNING' : logging.WARNING, 'ERROR' : logging.ERROR }
DIR_ATTRIBUTES = { 'st_atime': 0, 'st_ctime': 0, 'st_gid': os.getgid(),
                'st_mode': stat.S_IFDIR | 0755, 'st_mtime': 0, 'st_nlink': 1,
                'st_size': 0, 'st_uid': os.getuid() }
BAD_FILENAME_CHARS = set(":*?\"<>|")

class MTPFS(LoggingMixIn, Operations):   
   def __init__(self, mtp, mountpoint, is_debug=False, logger=None):
      global VERBOSE
      self.mtp = mtp
      self.is_debug = is_debug
      self.tempdir = tempfile.mkdtemp(prefix='pymtpfs')
      if not bool(self.tempdir) or not os.path.exists(self.tempdir):
         self.tempdir = tempfile.gettempdir()
      self.read_timeout = 2
      self.write_timeout = 2      
      self.openfile_t = namedtuple('openfile', 'handle, path, mtp_path, readonly')
      self.openfiles = {}
      self.log = logger
      self.created = LRU(1000) 
      if VERBOSE:         
         print("Mounted %s on %s" % (self.mtp, ))
      self.log.info("Mounted %s on %s" % (self.mtp, mountpoint))
   
   def __openfile_by_path(self, path):
      return next((en for en in self.openfiles.values() if en.mtp_path == path), None)

   def destroy(self, path):
      self.mtp.close()
      for openfile in self.openfiles.values():
         try:
            os.close(openfile.handle)
         except:
            self.log.exception("")
      try:
         if self.tempdir != tempfile.gettempdir():
            shutil.rmtree(self.tempdir)
      except:
         self.log.exception("")
      return 0
      
   def chmod(self, path, mode):
      return 0

   def chown(self, path, uid, gid):
      return 0
   
#   @log_calls
   def getattr(self, path, fh=None):
      attrib = {}
      path = fix_path(path, self.log)
      entry = self.mtp.get_path(path)
      if entry is None:
         entry = self.created.get(path)
      if entry is None:         
         raise FuseOSError(errno.ENOENT)
      else:
         try:
            attrib = entry.get_attributes()
         except Exception, e:
            self.log.exception("")
            attrib = {}
            exmess = ""
            try:
               exmess = str(e.message)
            except:
               exmess = "Unknown"
            self.log.error('Error reading MTP attributes for %s (%s)' % (path, exmess))
            raise FuseOSError(errno.ENOENT)            
      return attrib      
   
   def getxattr(self, path, name, position=0):
      return ""

   def create(self, path, mode):       
      path = fix_path(path, self.log)
      (fh, localpath) = self.__get_local_file(path)
      if fh < 0:
         raise FuseOSError(errno.EIO)
      openfile =  self.openfile_t(handle=fh, path=localpath, mtp_path=path, readonly=False)
      self.openfiles[fh] = openfile
      newfile = self.mtp.create(path)
      self.created[path] = newfile 
      return fh
      
   def open(self, path, flags):
      path = fix_path(path, self.log)
      is_readonly = ((flags & (os.O_WRONLY | os.O_RDWR)) == 0)
      ok = True
      (fh, localpath) = self.__get_local_file(path)
      if fh < 0:
         raise FuseOSError(errno.EIO)
      try:
         entry = self.mtp.get_path(path)
         if entry is None:
            entry = self.created.get(path)
            if not entry is None:               
               openfile =  self.openfile_t(handle=fh, path=localpath, mtp_path=path, readonly=is_readonly)
               self.openfiles[fh] = openfile      
               return fh         
         if entry is None and is_readonly:
            ok = False            
            raise FuseOSError(errno.ENOENT)
         if not entry is None and entry.is_directory():
            ok = False
            raise FuseOSError(errno.EISDIR)
         copyerr = self.mtp.copy_from(path, fh, timeout=self.__read_timeout(entry.get_length()))
         if copyerr != 0:
            if copyerr == errno.ENOENT and not is_readonly:
               pass
            else:
               ok = False
               raise FuseOSError(copyerr)
         openfile =  self.openfile_t(handle=fh, path=localpath, mtp_path=path, readonly=is_readonly)
         self.openfiles[fh] = openfile
      finally:
         if not ok:
            try:
               os.close(fh)
               os.remove(localpath)
            except:
               pass
      return fh
   

   def read(self, path, size, offset, fh):
      path = fix_path(path, self.log)
      global VERBOSE
      err = 0
      openfile = self.openfiles.get(fh)
      if openfile is None:
         if VERBOSE:
            sys.stderr.write('Error: handle %d not found in openfiles' % (fh,))
         self.log.error('Error: handle %d not found in openfiles' % (fh,))
         raise FuseOSError(errno.EBADF)
      try:
         if os.lseek(fh, offset, os.SEEK_SET) < 0:
            if VERBOSE:
               sys.stderr.write('Error: seek error to %d in %s (%s)' % (offset, openfile.path, openfile.mtp_path))
            self.log.error('Error: seek error to %d in %s (%s)' % (offset, openfile.path, openfile.mtp_path))
            err = errno.EINVAL
      except OSError, e:
         err = e.errno
         self.log.exception("")
      except:
         err = errno.EIO
         self.log.exception("")               
      if err != 0:
         raise FuseOSError(err)
      n = -1
      try:
         data = os.read(fh, size)
         retries = 0
         while len(data) < size:
            n = size - len(data)
            s = os.read(fh, n)
            if len(s) == 0:
               retries = retries + 1
               if retries > 2:
                  if VERBOSE:
                     sys.stderr.write("Short read (Expected %d got %d for %s)" % (size, len(data), path))
                  self.log.error("Short read (Expected %d got %d for %s)" % (size, len(data), path))
                  break
            else:
               data += s
      except OSError, e:
         self.log.exception("")
         err = e.errno         
      except:
         err = errno.EIO
         self.log.exception("")
      if err != 0:
         raise FuseOSError(err)
      return data
         
   def write(self, path, data, offset, fh):
      path = fix_path(path, self.log)
      global VERBOSE
      err = 0      
      openfile = self.openfiles.get(fh)
      if openfile is None or openfile.readonly:
         if VERBOSE:
            sys.stderr.write('Error: handle %d not found in openfiles' % (fh,))
         self.log.error('Error: handle %d not found in openfiles' % (fh,))
         raise FuseOSError(errno.EBADF)
      if os.lseek(fh, offset, os.SEEK_SET) < 0:
         if VERBOSE:
            sys.stderr.write('Error: seek error to %d in %s (%s)' % (offset, openfile.path, openfile.mtp_path))
         self.log.error('Error: seek error to %d in %s (%s)' % (offset, openfile.path, openfile.mtp_path))
         raise FuseOSError(errno.EOVERFLOW)
      n = -1
      try:
         n = os.write(fh, data)
         while n < len(data):
            n += os.write(fh, data[n:])
      except OSError, e:
         err = e.errno
         self.log.exception("")
      except:
         err = errno.EIO
         self.log.exception("")
      if err != 0:
         raise FuseOSError(err)         
      return n
      
   def release(self, path, fh):
      path = fix_path(path, self.log)      
      global VERBOSE
      err = 0
      try:
         os.close(fh)
      except:
         self.log.exception("")
      openfile = self.openfiles.get(fh)
      try:
         if not openfile is None:
            if not openfile.readonly:                       
               err = self.mtp.copy_to(openfile.path, openfile.mtp_path, timeout=self.__write_timeout(os.path.getsize(openfile.path)))
               if err != 0:
                  if VERBOSE:
                     sys.stderr.write('Error copying %s to %s' % (openfile.path, openfile.mtp_path))
                  self.log.error('Error copying %s to %s' % (openfile.path, openfile.mtp_path))                  
                  raise FuseOSError(err)  
               else:
                  try:
                     self.created.__delitem__(path)
                  except:
                     pass       
         else:
            if VERBOSE:
               sys.stderr.write('Error: handle %d not found in openfiles' % (fh,))
            self.log.error('Error: handle %d not found in openfiles' % (fh,))            
            raise FuseOSError(errno.EBADF)
      finally:
         self.__del(openfile.path)
      return 0
   
   def flush(self, path, fh):
      return self.fsync(path, 0, fh)
  
   def rename(self, oldpath, newpath):
      err = 0        
      oldpath = fix_path(oldpath, self.log)
      newpath = fix_path(newpath, self.log)
      oldentry = self.mtp.get_path(oldpath)
      if oldentry is None:
         raise FuseOSError(errno.ENOENT)      
      newentry = self.mtp.get_path(newpath)
      if oldentry.is_directory() and not newentry is None and newentry.is_directory() and newentry.object_count() > 0:
         raise FuseOSError(errno.ENOTEMPTY)
      if not self.mtp.rename(oldpath, newpath):
         raise FuseOSError(errno.EIO)
      return 0
      
   def fsync(self, path, datasync, fh):
      global VERBOSE
      path = fix_path(path, self.log)
      err = 0
      try:
         if bool(datasync):
            os.fdatasync(fh)
         else:
            os.fsync(fh)
      except OSError, e:
         err = e.errno
         self.log.exception(path)
#      if err == 0:
#         try:
#            openfile = self.openfiles.get(fh)
#            if not openfile is None and not openfile.readonly:
#               err = self.mtp.copy_to(openfile.path, openfile.mtp_path, timeout=self.__write_timeout(os.path.getsize(openfile.path)))
#               if err != 0:            
#                  if VERBOSE:
#                     sys.stderr.write('Error copying %s to %s' % (openfile.path, openfile.mtp_path))
#                  self.log.error('Error copying %s to %s' % (openfile.path, openfile.mtp_path))
#         except OSError, e:
#            self.log.exception("")
#            err = e.errno
#         except:
#            self.log.exception("")
#            err = errno.EIO
      if err != 0:
         raise FuseOSError(err)
      return 0

   def mkdir(self, path, mode):
      path = fix_path(path, self.log)
      entry = self.mtp.get_path(path)
      if not entry is None:
         raise FuseOSError(errno.EEXIST)
      dirpath, _ = os.path.split(path)
      entry = self.mtp.get_path(dirpath)
      if entry is None or not entry.is_directory():
         raise FuseOSError(errno.ENOTDIR)
      if self.mtp.mkdir(path):
         return 0
      else:
         self.log.error('Error creating directory ' + str(path))
         raise FuseOSError(errno.EIO)

   def rmdir(self, path):
      path = fix_path(path, self.log)
      entry = self.mtp.get_path(path)
      if entry is None:
         raise FuseOSError(errno.ENOENT)
      if not self.mtp.rmdir(path):
         raise FuseOSError(errno.EIO)
      return 0

   def mknod(self, path, mode, dev):
      path = fix_path(path, self.log)
      if (mode & stat.S_IFREG) != stat.S_IFREG:
         raise FuseOSError(errno.EINVAL)
      fh = self.create(path, mode)
      return self.release(path, fh)

   def readdir(self, path, fh):
      global DIR_ATTRIBUTES
      path = fix_path(path, self.log)
      err = 0
      offset = 0
      try:
         contents = [ ('.', copy.copy(DIR_ATTRIBUTES), offset),  ('..', copy.copy(DIR_ATTRIBUTES), offset) ]
         folder = self.mtp.get_path(path)
         if not folder is None:
            if not folder.is_directory():
               sys.stderr.write('%s is not a directory' % (path,))
            else:
               for en in folder.get_directories():
                  try:
                     name = utf8(en.get_name())
                  except:
                     self.log.exception(en.get_name())
                     continue
                  contents.append( (name, en.get_attributes(), offset) )
               for en in folder.get_files():
                  try:
                     name = utf8(en.get_name())
                  except:                     
                     self.log.exception(en.get_name())
                     continue
                  contents.append( (name, en.get_attributes(), offset) )
         return contents
      except OSError, e:
         self.log.exception("")
         err = e.errno
      except:
         self.log.exception("")
         err = errno.EIO
      raise FuseOSError(err)

   def unlink(self, path):
      path = fix_path(path, self.log)
      entry = self.mtp.get_path(path)
      if entry is None:
         raise FuseOSError(errno.ENOENT)
      if entry.is_directory():
         raise FuseOSError(errno.EISDIR)
      if not self.mtp.rm(path):
         raise FuseOSError(errno.EIO)
      return 0

   def truncate(self, path, length, fh=None):
      path = fix_path(path, self.log)
      openfile = None
      err = 0
      if fh is None:
         openfile = self.__openfile_by_path(path) # truncate seems to get called with a null handle after open for write
         if not openfile is None:
            fh = openfile.handle
      else:
         openfile = self.openfiles.get(fh)
      localpath = None      
      if openfile is None:         
         (fh, localpath) = self.__get_local_file(path)
         if fh < 0:
            raise FuseOSError(errno.EIO)         
         entry = self.mtp.get_path(path)
         is_created = False
         if entry is None:
            entry = self.created.get(path)
            is_created = (not entry is None)
         if entry is None:
            raise FuseOSError(errno.ENOENT)
         if entry.is_directory():
            raise FuseOSError(errno.EISDIR)
         if not is_created:                  
            err = self.mtp.copy_from(path, fh, timeout=self.__read_timeout(entry.get_length()))
         if err != 0:
            raise FuseOSError(err)
      os.ftruncate(fh, length)
      if openfile is None and not localpath is None and fh >= 0:
         try:
            os.lseek(fh, 0, os.SEEK_SET) 
         except OSError, e:
            self.log.exception("")
            err = e.errno
         if  err < 0:
            raise FuseOSError(errno.EIO)                  
         err = self.mtp.copy_to(fh, path, timeout=self.__write_timeout(length))
         if err != 0 :
            raise FuseOSError(err)
         try:
            os.close(fh)
         except:
            self.log.exception("")
         os.remove(localpath)                  
      return 0

   def utimens(self, path, times=None):
      path = fix_path(path, self.log)
      err = 0
      entry = self.mtp.get_path(path)
      if entry is None:
         entry = self.created.get(path)
         if not entry is None:
            return
      if entry is None:
         raise FuseOSError(errno.ENOENT)
      if entry.is_directory():
         return 0 # No-op as LIBMTP_folder_struct has no time fields
      (fh, localpath) = self.__get_local_file(path)      
      err = self.mtp.copy_from(path, fh, timeout=self.__read_timeout(entry.get_length()))
      try:
         os.close(fh)
      except OSError, e:
         err = e.errno
      if err == 0:
         ts = long(time.time()) if times is None else times[1] if len(times) > 1 else times[0] if len(times) > 0 else long(time.time())  
         err = self.mtp.copy_to(localpath, path, timestamp=ts, timeout=self.__write_timeout(os.path.getsize(localpath)))
      if err != 0:
         raise FuseOSError(err)
      return 0
      
   def __get_local_file(self, path):
      name, ext = os.path.splitext(os.path.split(path)[1])
      if name.strip() == '':
         name = 'tmp'
      else:
         name = name.strip('.')
      if ext.strip() == '':
         ext = '.tmp'
      try:
         (fh, localpath) = tempfile.mkstemp(prefix=name, suffix=ext, dir=self.tempdir)
      except:
         try:
            (fh, localpath) = tempfile.mkstemp(prefix='tmp', suffix='.tmp', dir=self.tempdir)
         except:
            localpath = os.path.join(self.tempdir, path.encode('ascii', 'ignore'))
            os.makedirs(os.path.split(localpath)[0])
            fh = os.open(localpath, os.O_WRONLY | os.O_CREAT)            
      if fh < 0:
         self.log.error("__get_local_file failed")
         return (fh, localpath, None)
      return (fh, localpath)      
   
   def __read_timeout(self, length):
      timeout = None
      try:
         timeout = self.read_timeout * length
         if timeout <= 10:
            timeout = 10
      except:
         timeout = None
      return timeout
   
   def __write_timeout(self, length):
      timeout = self.write_timeout * length
      if timeout < 10:
         timeout = 10
      return timeout
   
   def __del(self, path):
      try:
         os.remove(path)
      except:
         pass
   
def signal_handler(signum, frame):
   global VERBOSE, STOPPED, LOGGER
   if VERBOSE:
      print("Received signal " + str(signum))
   LOGGER.warn("Received signal " + str(signum))
   STOPPED = True

def configure_logger(level, path=None, maxsize=1024 * 1024 * 10, count=1):
   logger = logging.getLogger("pymtpfs")
   print path, maxsize, count
   logger.setLevel(level)   
   if path is None:         
      handler = logging.handlers.SysLogHandler(facility=logging.handlers.SysLogHandler.LOG_DAEMON)
      formatter = logging.Formatter('%(filename)s: %(levelname)s %(module)s.%(funcName)s:%(lineno)d %(message)s')
      handler.setFormatter(formatter)
      logger.addHandler(handler)
   else:
      handler = logging.handlers.RotatingFileHandler(path, mode='a', maxBytes=maxsize, backupCount=count)
      formatter = logging.Formatter("%(asctime)s - %(module)s.%(funcName)s:%(lineno)d - %(levelname)s - %(message)s")
      handler.setFormatter(formatter)
      logger.addHandler(handler)
      logger.info('Logging to %s, max file size %d, number of files %d' % (path, maxsize, count))
   return logger

def logger_options(options):
   global VERBOSE
   if options.nolog:
      logger = logging.getLogger("pymtpfs")
      logger.addHandler(logging.NullHandler())
      return logger
   loglevel = logging.ERROR
   logpath = None
   maxsize = 1024 * 1024 * 10
   logcount = 1
   slevel = options.loglevel.strip().upper()      
   loglevel = LOG_LEVELS.get(slevel)
   if loglevel is None:
      sys.stderr.write('Argument error for -e (--loglevel) %s. Argument must be one of %s' % (options.loglevel, str(LOG_LEVELS.keys())))
      return None
   if not options.log is None:         
      logargs = options.log.split(',')
      logpath = logargs[0]         
      maxsizes = ''         
      if logpath.strip() == '':
         sys.stderr.write('Argument error for -L (--log) %s. No log file specified' % (options.log,))
         return None
      dir, logfilename = os.path.split(logpath)
      if dir.strip() != '' and not os.path.exists(dir):
         answer = raw_input('Logging directory %s does not exist. Do you want to create it (Y/N)?' % (dir,))
         if answer.strip().lower() == 'y':
            os.makedirs(dir)
         else:
            return None
      if len(logargs) >= 2:
         try:
            maxsizes = logargs[1].strip()
            maxsize = int(maxsizes) * 1024 * 1024
         except:
            sys.stderr.write('Argument error for -L (--log) %s. Max size must be a integer' % (options.log,))
            return None
      if len(logargs) >= 3:
         try:
            logcount = int(logargs[2].strip())
         except:
            sys.stderr.write('Argument error for -L (--log) %s. Count of logfiles must be a integer' % (options.log,))            
            return None
      if VERBOSE:
         s = ''
         if logcount > 1:
            s = '[0-%d]' % (logcount-1)
         print("Logging to directory %s file %s%s level %s. Max log file size %sM (%d bytes)" % (dir, logfilename, s, maxsizes, maxsize))
   return configure_logger(loglevel, logpath, maxsize, logcount)
   
def main(argv=None):  
   global STOPPED, VERSION, VERBOSE, DEBUG, LOGGER  
   if argv is None:
      argv = sys.argv
   usage="""%s Version %s
%s [-vDNLes] [device] mountpoint (If device not specified first available device is mounted)
%s -l (List available devices)
""" % (argv[0], VERSION, argv[0], argv[0])
   parser = OptionParser(usage=usage)
   parser.add_option("-v", '--verbose', action="store_true", dest="verbose", \
                     help="Verbose", default=False)  
   parser.add_option("-D", '--debug', action="store_true", dest="debug", \
                     help="Debug mode", default=False)
   parser.add_option("-N", '--nolog', action="store_true", dest="nolog", \
                     help="No logging whatsoever (not even errors to syslog)", default=False)
   parser.add_option("-L", '--log',  dest="log",  default=None,\
                     help="""Logging options. Argument format is filename,maxsize-in-Mb,count 
                     Filename can be an absolute path in which case the directory of the file is 
                     used for log files eg /root/logs/pymtpfs/pymtpfs.log or it can be relative to the
                     current directory eg logs/pymtpfs.log. If there is no directory in the path
                     then the current directory is used for logging.
                     Default for maxsize is 10Mb
                     Default for count is 1 
                     eg -L logs/pymtpfs.log,8,5 results in up to 5 log files named pymtpfs.log.[0-4] 
                     with a max size of 8Mb per file""")
   parser.add_option("-e", '--loglevel',  dest="loglevel",  default="ERROR",\
                     help="""Log Level. One of %s eg
                     -e %s
                     """ % (str(LOG_LEVELS.keys()), LOG_LEVELS.keys()[0]))
   parser.add_option("-l", '--list', action="store_true", dest="list", \
                     help="List available MTP devices and exit", default=False)
   (options, args) = parser.parse_args()
   VERBOSE = options.verbose
   DEBUG = options.debug
   signal.signal(signal.SIGTERM, signal_handler)
   signal.signal(signal.SIGQUIT, signal_handler)
   mountpoint = deviceid = None
   if not options.list:
      if len(args) == 1:
         mountpoint = os.path.abspath(args[0])
      elif len(args) == 2:
         deviceid = args[0]
         mountpoint = os.path.abspath(args[1])
      else:
         parser.print_help()
         return 1
      if not os.path.exists(mountpoint):
         sys.stderr.write('Mount point %s does not exist' % (mountpoint,))
         return 1
      if not os.path.isdir(mountpoint):
         sys.stderr.write('Mount point %s is not a directory' % (mountpoint,))
         return 1
      if len(os.listdir(mountpoint)) > 0:
         sys.stderr.write('Mount point %s is not empty' % (mountpoint,))
         return 1
            
   LOGGER = logger = logger_options(options)
   if logger is None:
      parser.print_help()
      return 1             
 
   mtp = MTP(DEBUG)   
   if mtp is None:
      print("Could not open MTP")
      return 1
   result = mtp.get_last_error()
   if result == 5 or mtp.count() == 0:
      sys.stderr.write('No MTP devices connected')
      return 1
   elif result != 0:
      sys.stderr.write('MTP error (%d' % (result,))
   if options.list or deviceid is None:      
      if options.list:
         devices = mtp.devices
         for device in devices:
            print(device)
         return 0
      else:
         if mtp.open(0):
            print(mtp)
   else:
      if not deviceid is None:
         if not mtp.open(deviceid):
            sys.stderr.write('Could not find a MTP device matching %s. Try running with the -l option to get device ids' % (deviceid,))
            return 1
         else:
            print mtp
   
   fuse = FUSE(MTPFS(mtp, mountpoint, is_debug=options.debug, logger=logger), mountpoint, encoding='utf-8', foreground=True, nothreads=True)

def fix_path(path, logger=None):
   if any((c in BAD_FILENAME_CHARS) for c in path):
      newpath = path
      for ch in BAD_FILENAME_CHARS:
         newpath = newpath.replace(ch, '-')
      if not logger is None:
         logger.warn("Transformed path %s to %s" % (path, newpath))
      path = newpath
   return utf8(path, logger)

def utf8(path, logger=None):
   if type(path) == str:
#      try:
#         newpath = path.encode('utf-8')
#         path = newpath
#      except:
      try:
         newpath = unicode(path, encoding='utf-8', errors='ignore')
         path = newpath                        
      except:
         if not logger is None:
            logger.exception(path)
   return path
   
def log_calls(f):
   @wraps(f)
   def wrapped(*args, **kwargs):
      global DEBUG
      call_string = "%s called with *args: %r, **kwargs: %r " % (f.__name__, args, kwargs)
      try:
         retval = f(*args, **kwargs)
         if DEBUG:
            call_string += " --> " + repr(retval)
            print call_string
         return retval
      except Exception, e:
         top = traceback.extract_stack()[-1]   # get traceback info to print out later
         call_string += " RAISED EXCEPTION: "
         call_string += ", ".join([type(e).__name__, os.path.basename(top[0]), str(top[1])])
         print call_string
         raise
   return wrapped

if __name__ == "__main__":
   sys.exit(main())
