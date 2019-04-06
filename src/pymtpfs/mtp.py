'''
@author: Donald Munro
License: Apache V2 (http://www.apache.org/licenses/LICENSE-2.0.txt)
Provides a file system based Python ctypes language abstraction for MTP devices
'''

import codecs
import copy
import errno
import logging
import os
import signal
import stat
import sys
import tempfile
import time
import traceback
from builtins import FileNotFoundError
from ctypes import *
from ctypes.util import find_library
from datetime import datetime
from io import StringIO
from typing import Tuple, List, Optional

import lru_py.lru as LRU
from past.builtins import unicode
from past.types import long
from typed_ast._ast3 import Dict

PATH_CACHE_SIZE = 10000


class LIBMTP_device_entry_struct(Structure):
    _fields_ = [('vendor', c_char_p),
                ('vendor_id', c_uint16),
                ('product', c_char_p),
                ('product_id', c_uint16),
                ('device_flags', c_uint32)
                ]


class LIBMTP_raw_device_struct(Structure):
    _fields_ = [('device_entry', LIBMTP_device_entry_struct),
                ('bus_location', c_uint32),
                ('devnum', c_uint8)
                ]


class LIBMTP_devicestorage_struct(Structure):
    @property
    def StorageDescriptionStr(self) -> str:
        return self.StorageDescription.decode("utf-8")


LIBMTP_devicestorage_struct._fields_ = \
    [('id', c_uint32),
     ('StorageType', c_uint16),
     ('FilesystemType', c_uint16),
     ('AccessCapability', c_uint16),
     ('MaxCapacity', c_uint64),
     ('FreeSpaceInBytes', c_uint64),
     ('FreeSpaceInObjects', c_uint64),
     ('StorageDescription', c_char_p),
     ('VolumeIdentifier', c_char_p),
     ('next', POINTER(LIBMTP_devicestorage_struct)),
     ('prev', POINTER(LIBMTP_devicestorage_struct))
     ]


class LIBMTP_device_extension_struct(Structure):
    pass


LIBMTP_device_extension_struct._fields_ = \
    [('name', c_char_p),
     ('major', c_int),
     ('minor', c_int),
     ('next', POINTER(LIBMTP_device_extension_struct))
     ]


class LIBMTP_mtpdevice_struct(Structure):
    pass


LIBMTP_mtpdevice_struct._fields_ = \
    [('object_bitsize', c_uint8),
     ('params', c_void_p),
     ('usbinfo', c_void_p),
     ('storage', POINTER(LIBMTP_devicestorage_struct)),
     ('errorstack', c_void_p),
     ('maximum_battery_level', c_uint8),
     ('default_music_folder', c_uint32),
     ('default_playlist_folder', c_uint32),
     ('default_picture_folder', c_uint32),
     ('default_video_folder', c_uint32),
     ('default_organizer_folder', c_uint32),
     ('default_zencast_folder', c_uint32),
     ('default_album_folder', c_uint32),
     ('default_text_folder', c_uint32),
     ('cd', c_void_p),
     ('extensions', POINTER(LIBMTP_device_extension_struct)),
     ('cached', c_int),
     ('next', POINTER(LIBMTP_mtpdevice_struct))
     ]


class LIBMTP_folder_struct(Structure):
    pass


LIBMTP_folder_struct._fields_ = \
    [('folder_id', c_uint32),
     ('parent_id', c_uint32),
     ('storage_id', c_uint32),
     ('name', c_char_p),
     ('sibling', POINTER(LIBMTP_folder_struct)),
     ('child', POINTER(LIBMTP_folder_struct))
     ]


class LIBMTP_file_struct(Structure):
    @property
    def name_str(self) -> str:
        return self.name.decode("utf-8")


LIBMTP_file_struct._fields_ = \
    [('item_id', c_uint32),
     ('parent_id', c_uint32),
     ('storage_id', c_uint32),
     ('name', c_char_p),
     ('filesize', c_uint64),
     ('modificationdate', c_long),
     ('filetype', c_int),
     ('next', POINTER(LIBMTP_file_struct))
     ]


class MTPDevice:
    def __init__(self, vendor_id, product_id, vendor, product, device=None):
        self.vendor_id = int(vendor_id)
        self.product_id = int(product_id)
        self.device = device
        if product is None:
            if self.product_id == 0x4b54:
                self.product = 'Fenix 5/5X/5S Plus'
            else:
                self.product = 'UNKNOWN'
        else:
            self.product = product
        if vendor is None:
            if self.vendor_id == 0x091e:
                self.vendor = 'Garmin'
            else:
                self.vendor = 'UNKNOWN'
        else:
            self.vendor = vendor

    def set_mtp_device(self, mtpdev):
        self.device = mtpdev

    def __str__(self):
        dev = ""
        if not self.device is None:
            dev = " at %X" % (id(self.device),)
        return "%04x:%04x  %-20s %-40s %s" % (self.vendor_id, self.product_id, self.vendor, self.product, dev)


class MTPType:
    LIBMTP_FILETYPE_FOLDER = 0
    LIBMTP_FILETYPE_WAV = LIBMTP_FILETYPE_FOLDER + 1
    LIBMTP_FILETYPE_MP3 = LIBMTP_FILETYPE_FOLDER + 2
    LIBMTP_FILETYPE_WMA = LIBMTP_FILETYPE_FOLDER + 3
    LIBMTP_FILETYPE_OGG = LIBMTP_FILETYPE_FOLDER + 4
    LIBMTP_FILETYPE_AUDIBLE = LIBMTP_FILETYPE_FOLDER + 5
    LIBMTP_FILETYPE_MP4 = LIBMTP_FILETYPE_FOLDER + 6
    LIBMTP_FILETYPE_UNDEF_AUDIO = LIBMTP_FILETYPE_FOLDER + 7
    LIBMTP_FILETYPE_WMV = LIBMTP_FILETYPE_FOLDER + 8
    LIBMTP_FILETYPE_AVI = LIBMTP_FILETYPE_FOLDER + 9
    LIBMTP_FILETYPE_MPEG = LIBMTP_FILETYPE_FOLDER + 10
    LIBMTP_FILETYPE_ASF = LIBMTP_FILETYPE_FOLDER + 11
    LIBMTP_FILETYPE_QT = LIBMTP_FILETYPE_FOLDER + 12
    LIBMTP_FILETYPE_UNDEF_VIDEO = LIBMTP_FILETYPE_FOLDER + 13
    LIBMTP_FILETYPE_JPEG = LIBMTP_FILETYPE_FOLDER + 14
    LIBMTP_FILETYPE_JFIF = LIBMTP_FILETYPE_FOLDER + 15
    LIBMTP_FILETYPE_TIFF = LIBMTP_FILETYPE_FOLDER + 16
    LIBMTP_FILETYPE_BMP = LIBMTP_FILETYPE_FOLDER + 17
    LIBMTP_FILETYPE_GIF = LIBMTP_FILETYPE_FOLDER + 18
    LIBMTP_FILETYPE_PICT = LIBMTP_FILETYPE_FOLDER + 19
    LIBMTP_FILETYPE_PNG = LIBMTP_FILETYPE_FOLDER + 20
    LIBMTP_FILETYPE_VCALENDAR1 = LIBMTP_FILETYPE_FOLDER + 21
    LIBMTP_FILETYPE_VCALENDAR2 = LIBMTP_FILETYPE_FOLDER + 22
    LIBMTP_FILETYPE_VCARD2 = LIBMTP_FILETYPE_FOLDER + 23
    LIBMTP_FILETYPE_VCARD3 = LIBMTP_FILETYPE_FOLDER + 24
    LIBMTP_FILETYPE_WINDOWSIMAGEFORMA = LIBMTP_FILETYPE_FOLDER + 25
    LIBMTP_FILETYPE_WINEXEC = LIBMTP_FILETYPE_FOLDER + 26
    LIBMTP_FILETYPE_TEXT = LIBMTP_FILETYPE_FOLDER + 27
    LIBMTP_FILETYPE_HTML = LIBMTP_FILETYPE_FOLDER + 28
    LIBMTP_FILETYPE_FIRMWARE = LIBMTP_FILETYPE_FOLDER + 29
    LIBMTP_FILETYPE_AAC = LIBMTP_FILETYPE_FOLDER + 30
    LIBMTP_FILETYPE_MEDIACARD = LIBMTP_FILETYPE_FOLDER + 31
    LIBMTP_FILETYPE_FLAC = LIBMTP_FILETYPE_FOLDER + 32
    LIBMTP_FILETYPE_MP2 = LIBMTP_FILETYPE_FOLDER + 33
    LIBMTP_FILETYPE_M4A = LIBMTP_FILETYPE_FOLDER + 34
    LIBMTP_FILETYPE_DOC = LIBMTP_FILETYPE_FOLDER + 35
    LIBMTP_FILETYPE_XML = LIBMTP_FILETYPE_FOLDER + 36
    LIBMTP_FILETYPE_XLS = LIBMTP_FILETYPE_FOLDER + 37
    LIBMTP_FILETYPE_PPT = LIBMTP_FILETYPE_FOLDER + 38
    LIBMTP_FILETYPE_MHT = LIBMTP_FILETYPE_FOLDER + 39
    LIBMTP_FILETYPE_JP2 = LIBMTP_FILETYPE_FOLDER + 40
    LIBMTP_FILETYPE_JPX = LIBMTP_FILETYPE_FOLDER + 41
    LIBMTP_FILETYPE_ALBUM = LIBMTP_FILETYPE_FOLDER + 42
    LIBMTP_FILETYPE_PLAYLIST = LIBMTP_FILETYPE_FOLDER + 43
    LIBMTP_FILETYPE_UNKNOWN = LIBMTP_FILETYPE_FOLDER + 44

    dict = {'wav': LIBMTP_FILETYPE_WAV, 'mp3': LIBMTP_FILETYPE_MP3, 'wma': LIBMTP_FILETYPE_WMA,
            'ogg': LIBMTP_FILETYPE_OGG,
            'mp4': LIBMTP_FILETYPE_MP4, 'wmv': LIBMTP_FILETYPE_WMV, 'avi': LIBMTP_FILETYPE_AVI,
            'mpeg': LIBMTP_FILETYPE_MPEG,
            'asf': LIBMTP_FILETYPE_ASF, 'qt': LIBMTP_FILETYPE_QT, 'jpeg': LIBMTP_FILETYPE_JPEG,
            'jfif': LIBMTP_FILETYPE_JFIF,
            'tiff': LIBMTP_FILETYPE_TIFF, 'bmp': LIBMTP_FILETYPE_BMP, 'gif': LIBMTP_FILETYPE_GIF,
            'pict': LIBMTP_FILETYPE_PICT,
            'png': LIBMTP_FILETYPE_PNG, 'text': LIBMTP_FILETYPE_TEXT, 'txt': LIBMTP_FILETYPE_TEXT,
            'html': LIBMTP_FILETYPE_HTML,
            'aac': LIBMTP_FILETYPE_AAC, 'flac': LIBMTP_FILETYPE_FLAC, 'mp2': LIBMTP_FILETYPE_MP2,
            'm4a': LIBMTP_FILETYPE_M4A,
            'doc': LIBMTP_FILETYPE_DOC, 'xml': LIBMTP_FILETYPE_XML, 'xls': LIBMTP_FILETYPE_XLS,
            'ppt': LIBMTP_FILETYPE_PPT,
            'mht': LIBMTP_FILETYPE_MHT, 'jp2': LIBMTP_FILETYPE_JP2, 'jpx': LIBMTP_FILETYPE_JPX,
            'ogg': LIBMTP_FILETYPE_AUDIBLE, 'ape': LIBMTP_FILETYPE_AUDIBLE}

    @staticmethod
    def filetype(path):
        ext = os.path.splitext(path)[1].lower()[1:]
        return MTPType.dict.get(ext, MTPType.LIBMTP_FILETYPE_UNKNOWN)


def utf8(path, logger=None):
    if type(path) == str:
        try:
            newpath = unicode(path, encoding='utf-8', errors='ignore')
            path = newpath
        except:
            if not logger is None:
                logger.exception(path)
    return path


class MTPEntry:
    def __init__(self, id, path, folderid=-2, storageid=-2, timestamp=0, length=0):
        self.id = id
        self.folderid = folderid
        self.storageid = storageid
        self.path = path
        self.name = os.path.split(path)[1]
        self.timestamp = timestamp
        self.datetime = datetime.fromtimestamp(timestamp)
        self.length = length
        self.log = logging.getLogger("pymtpfs")

    def get_id(self):
        return self.id

    def get_folder_id(self):
        return self.folderid

    def get_storage_id(self):
        return self.storageid

    def get_path(self):
        return self.path

    def get_name(self):
        return self.name

    def get_timestamp(self):
        return self.timestamp

    def get_length(self):
        return self.length

    def is_directory(self):
        raise NotImplementedError("is_directory")

    def get_attributes(self):
        raise NotImplementedError("get_attributes")

    def get_directories(self):
        return ()

    def get_files(self):
        return ()

    def add_file(self, file):
        pass

    def __str__(self):
        s = "%-35s%-12s%5s%10d" % (
            self.name, self.datetime.strftime("%d %B %Y %I:%M%p") if not self.datetime is None else '',
            '<dir>' if self.is_directory else '', self.length)


class MTPRefresh:
    def __init__(self, must_refresh=True):
        self.must_refresh = True

    def refresh(self):
        raise NotImplementedError("refresh")


class MTPFile(MTPEntry):
    def __init__(self, id, path, storageid=-2, folderid=-2, dt=0, length=0):
        MTPEntry.__init__(self, id, path, folderid, storageid, dt, length)

    def is_directory(self):
        return False

    def get_attributes(self):
        return {'st_atime': self.timestamp, 'st_ctime': self.timestamp, 'st_gid': os.getgid(),
                'st_mode': stat.S_IFREG | 0o755, 'st_mtime': self.timestamp, 'st_nlink': 1,
                'st_size': self.length, 'st_uid': os.getuid()}

    def __str__(self):
        return "<MTPFile %s>" % self.path


class MTPFolder(MTPEntry, MTPRefresh):
    files: List[MTPFile]

    def __init__(self, path, id=-2, storageid=-2, folderid=-2, mtp=None, timestamp=0, is_refresh=True):
        MTPEntry.__init__(self, id=id, path=path, folderid=folderid, storageid=storageid, timestamp=timestamp)
        MTPRefresh.__init__(self)
        self.directories = []
        self.files = []
        self.mtp = mtp
        self.writable = False
        if folderid >= -1 and is_refresh:
            self.writable = True
            self.refresh()

    def refresh(self):
        self.log.debug("refresh(%s, %d, %d)" % (self.path, self.storageid, self.folderid))
        readf = self.mtp.libmtp.LIBMTP_Get_Files_And_Folders
        readf.restype = POINTER(LIBMTP_file_struct)
        pfile = None
        self.directories = []
        self.files = []
        try:
            pfile = readf(self.mtp.open_device.device, self.storageid, self.id)
            if not bool(pfile):
                return False
            pf = pfile
            while bool(pf):
                if pf[0].filetype == 0:
                    dir = MTPFolder(path=os.path.join(self.path, pf[0].name_str), id=pf[0].item_id,
                                    storageid=self.storageid,
                                    folderid=pf[0].parent_id, mtp=self.mtp, timestamp=pf[0].modificationdate,
                                    is_refresh=False)
                    self.directories.append(dir)
                else:
                    self.files.append(
                        MTPFile(pf[0].item_id, os.path.join(self.path, pf[0].name_str), self.storageid, self.folderid,
                                pf[0].modificationdate, pf[0].filesize))
                pf = pf[0].next
            self.must_refresh = False
            return True
        finally:
            if not pfile is None:
                self.mtp.libmtp.LIBMTP_destroy_file_t(pfile)

    def find_directory(self, dirname):
        dir = next((dir for dir in self.directories if utf8(dir.get_name()) == utf8(dirname)), None)
        if not dir is None and dir.must_refresh:
            dir.refresh()
        return dir

    def find_file(self, filename):
        #      return next((f for f in self.files if utf8(f.get_name()) == utf8(filename)), None)
        for f in self.files:
            s1 = utf8(f.get_name())
            s2 = utf8(filename)
            if s1 == s2:
                return f
        return None

    def close(self):
        pass

    def is_directory(self):
        return True

    def get_attributes(self):
        return {'st_atime': self.timestamp, 'st_ctime': self.timestamp, 'st_gid': os.getgid(),
                'st_mode': stat.S_IFDIR | 0o755, 'st_mtime': self.timestamp, 'st_nlink': 1,
                'st_size': 0, 'st_uid': os.getuid()}

    def get_directories(self):
        return copy.copy(self.directories)

    def get_files(self):
        return copy.copy(self.files)

    def add_file(self, file):
        self.files.append(file)

    def object_count(self):
        return len(self.directories) + len(self.files)

    def __str__(self):
        return "<MTPFolder(path:'%s' folderId:%s)>" % (self.path, self.folderid)


class MTPStorage(MTPEntry, MTPRefresh):
    directories: Optional[List[MTPFolder]]

    def __init__(self, mtp: 'MTP', pstorage=None):
        global PATH_CACHE_SIZE
        MTPRefresh.__init__(self)
        self.mtp = mtp
        self.libmtp = mtp.libmtp
        self.open_device = mtp.open_device
        self.directories = None
        self.contents = LRU.LRU(PATH_CACHE_SIZE)
        if pstorage is None:
            MTPEntry.__init__(self, -3, '/')
            self.storage = None
            self.directories = []
            for dirname in self.mtp.get_storage_descriptions():
                # def __init__(self, path, id=-2, storageid=-2, folderid=-2, mtp=None, timestamp=0, is_refresh=True):
                self.directories.append(MTPFolder(path=dirname, id=-3, storageid=-3, folderid=-2, is_refresh=False))
            self.root = None
            self.contents[utf8(os.sep)] = self
        else:
            self.storage = pstorage
            storage = pstorage.contents
            self.type = storage.StorageType
            self.freespace = storage.FreeSpaceInBytes
            self.capacity = storage.MaxCapacity
            path = os.sep + storage.StorageDescriptionStr
            MTPEntry.__init__(self, storage.id, path, storageid=None, folderid=0)
            self.root = MTPFolder(path=path, id=0, storageid=storage.id, folderid=0, mtp=self.mtp)
            self.contents[utf8(path)] = self.root

    def is_directory(self):
        return True

    def get_attributes(self):
        return {'st_atime': self.timestamp, 'st_ctime': self.timestamp, 'st_gid': os.getgid(),
                'st_mode': stat.S_IFDIR | 0o755, 'st_mtime': self.timestamp, 'st_nlink': 1,
                'st_size': 0, 'st_uid': os.getuid()}

    def get_directories(self) -> List[MTPFolder]:
        if self.directories is None:
            if self.root is None:
                return []
            else:
                return self.root.get_directories()
        else:
            return self.directories

    def get_files(self):
        if self.root is None:
            return ()
        else:
            return self.root.get_files()

    def add_file(self, file):
        if not self.root is None:
            self.root.add_file(self, file)

    def __str__(self):
        s = "MTPStorage %s: id=%d, device=%s%s" % (self.name, self.id, self.open_device, os.linesep)
        return s

    def find_entry(self, path):
        path = utf8(path)
        self.log.debug('find_entry(%s)' % (path,))

        if path.strip() == '':
            path = os.sep + self.name
        try:
            entry = self.contents[path]
            if entry.is_directory() and entry.must_refresh:
                entry.refresh()
        except KeyError:
            components = [comp for comp in path.split(os.sep) if len(comp.strip()) != 0]
            if len(components) == 0:
                return None
            if components[0] != self.name:
                raise LookupError('Invalid storage (expected %s, was %s)' % (self.name, components[0]))
            entry = self.__find_entry(self.root, components[1:])

        return entry

    def __find_entry(self, entry, components):
        self.log.debug("__find_entry(%s, %s)" % (entry, str(components)))
        if len(components) == 0:
            return entry
        name = components[0]
        path = entry.path + os.sep + name
        try:
            en = self.contents[utf8(path)]
            if not en is None:
                if en.is_directory() and en.must_refresh:
                    en.refresh()
                return self.__find_entry(en, components[1:])
        except KeyError:
            en = entry.find_directory(name)
            if not en is None and en.is_directory():
                self.contents[utf8(path)] = en
                if en.must_refresh:
                    en.refresh()
                return self.__find_entry(en, components[1:])
            return entry.find_file(name)

    def remove_entry(self, path):
        try:
            del self.contents[utf8(path)]
            return True
        except KeyError:
            return False

    def refresh(self):
        if not self.root is None and self.must_refresh:
            self.must_refresh = not self.root.refresh()

    def close(self):
        pass


class MTP:
    global MTP_PATH
    MTP_PATH = find_library('mtp')
    if not MTP_PATH:
        raise EnvironmentError('Unable to find libmtp')
    CDLL(MTP_PATH).LIBMTP_Init()

    PROGRESS_FUNC_P = CFUNCTYPE(c_uint64, c_uint64, c_void_p)

    def __init__(self, is_debug=False):
        global MTP_PATH
        self.libmtp = CDLL(MTP_PATH)
        self.libc = CDLL(find_library('c'))
        self.device_no = -1
        self.pdevices = POINTER(LIBMTP_raw_device_struct)()
        self.cdevices = c_int(0)
        self.devices: List[MTPDevice] = []
        self.storages: Dict[str, MTPStorage] = dict()
        self.open_device: Optional[MTPDevice] = None
        self.last_error = 0
        self.last_error_message = "OK"
        self.refresh()
        self.is_debug = is_debug
        self.log = logging.getLogger("pymtpfs")
        self.log.info('MTP init')

    def refresh(self) -> Tuple[MTPDevice]:
        self.last_error = self.libmtp.LIBMTP_Detect_Raw_Devices(byref(self.pdevices), pointer(self.cdevices))
        if self.last_error == 5 or self.cdevices.value == 0:
            self.devices = []
            return tuple(self.devices)
        elif self.last_error != 0:
            self.devices = []
            return ()
        no = self.cdevices.value
        self.devices = []
        for i in range(0, no):
            self.devices.append(
                MTPDevice(self.pdevices[i].device_entry.vendor_id, self.pdevices[i].device_entry.product_id,
                          self.pdevices[i].device_entry.vendor, self.pdevices[i].device_entry.product))
        return tuple(self.devices)

    def check(self):
        if self.open_device is None or self.open_device.device is None or not bool(self.open_device.device):
            return False
        err = self.libmtp.LIBMTP_Get_Storage(self.open_device.device, 0)
        if err != 0:
            self.last_error = err
            self.last_error_message = "Error getting MTP Storage(s) (check connection)"
            self.log.error("Device check error")
            return False
        return True

    def count(self):
        return len(self.devices)

    def open(self, devno, must_refresh=False) -> bool:
        if self.devices is None or len(self.devices) == 0 or must_refresh:
            self.refresh()
        if type(devno) == str or type(devno) == unicode:
            l = devno.split(':')
            if len(l) < 2:
                devno = int(l[0])
            else:
                (vid, pid) = [int(it.strip(), 16) for it in l]
                devno = -1
                for i in range(0, len(self.devices)):
                    if self.pdevices[i].device_entry.vendor_id == vid and self.pdevices[
                        i].device_entry.product_id == pid:
                        devno = i
                        break
        if type(devno) == int or type(devno) == long:
            if devno >= 0:
                vendorid = self.pdevices[devno].device_entry.vendor_id
                productid = self.pdevices[devno].device_entry.product_id
                self.deviceid = "%04x:%04x" % (vendorid, productid)
                self.device_no = devno
                openDevice = self.libmtp.LIBMTP_Open_Raw_Device_Uncached
                openDevice.restype = POINTER(LIBMTP_mtpdevice_struct)
                POINTER(LIBMTP_mtpdevice_struct)()
                device = openDevice(byref(self.pdevices[devno]))
                if not (device) or device is None or device.contents is None:
                    self.last_error_message = "Error Opening MTP open_device %s" % (self.deviceid,)
                    self.open_device = None
                    return False

                self.last_error = self.libmtp.LIBMTP_Get_Storage(device, 0)
                if self.last_error != 0:
                    self.last_error_message = "Error getting MTP Storage(s)"
                    self.libmtp.LIBMTP_Dump_Errorstack(device);
                    self.libmtp.LIBMTP_Clear_Errorstack(device)
                    self.libmtp.LIBMTP_Release_Device(device)
                    self.open_device = None
                    return False

                self.open_device = self.devices[devno]
                self.open_device.set_mtp_device(device)
                self.open_device.vendor_id = vendorid
                self.open_device.product_id = productid
                pstorage = device.contents.storage
                while bool(pstorage):
                    newstorage = MTPStorage(self, pstorage)
                    self.storages[pstorage[0].StorageDescriptionStr] = newstorage
                    pstorage = pstorage[0].next
                rootstorage = MTPStorage(self, None)
                self.storages[os.sep] = rootstorage
                return True
        return False

    def close(self):
        #      for storage in self.storages.values():
        #         storage.close()
        self.storages.clear()
        self.devices = None
        if not self.open_device is None and not self.open_device.device is None and not self.open_device.device.contents is None:
            self.log.info('Releasing device')
            self.libmtp.LIBMTP_Release_Device(self.open_device.device)
        self.open_device = None
        return True

    def get_storage(self, path: Optional[str] = None) -> Optional[MTPStorage]:
        if self.open_device is None:
            return None
        if path is None or path.strip().replace(os.sep, '') == '':
            return self.storages.get(os.sep)
        components = [comp for comp in path.split(os.sep) if len(comp.strip()) != 0]
        if len(components) == 0:
            return None
        return self.storages.get(components[0])

    def get_storage_descriptions(self) -> Optional[List[str]]:
        if self.open_device is None:
            return None
        return [s for s in self.storages.keys()]

    def copy_from(self, source, target, timeout=None, recurse=0):
        timeouterr = [0, ]

        def timeout_handler(sig, frame):
            timeouterr[0] = errno.EINTR

        entry = self.get_path(source)
        if entry is None:
            return errno.ENOENT
        if entry.is_directory():
            return errno.EISDIR
        oldhandler = None
        if not timeout is None:
            oldhandler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout)
        try:
            self.libmtp.LIBMTP_Clear_Errorstack(self.open_device.device)
            if type(target) == str or type(target) == unicode:
                ret = self.libmtp.LIBMTP_Get_File_To_File(self.open_device.device, entry.get_id(), c_char_p(target),
                                                          None, None)
            else:
                ret = self.libmtp.LIBMTP_Get_File_To_File_Descriptor(self.open_device.device, entry.get_id(), target,
                                                                     None, None)
            #                                                                 MTP.PROGRESS_FUNC_P(), c_void_p())
            signal.alarm(0)
        finally:
            if not oldhandler is None:
                signal.signal(signal.SIGALRM, oldhandler)
        if ret != 0:
            try:
                sys.stderr.write(str(source) + ' ->  ' + str(target) + os.linesep)
            except:
                pass
            self.libmtp.LIBMTP_Dump_Errorstack(self.open_device.device)
        if timeouterr[0] != 0:
            if timeouterr[0] != 0:
                self.log.error("Timeout transferring %s to %s in %d seconds" % (str(source), str(target), timeout))
            else:
                self.log.error("Error transferring %s to %s" % (str(source), str(target)))
            devno = "%04x:%04x" % (self.open_device.vendor_id, self.open_device.product_id)
            if recurse == 0:
                #            self.log.warn('Resetting device ' +  devno)
                #            ret = self.libmtp.LIBMTP_Reset_Device(self.open_device.device)
                self.close()
                if self.open(devno, must_refresh=False):
                    return self.copy_from(source, target, timeout, recurse=1)
                else:
                    recurse = 1
            if recurse == 1:
                self.log.error('Reset device failed. Attempting to reopen')
                self.close()
                if not self.open(devno, must_refresh=True):
                    self.log.error("Could not reopen device.")
                    return errno.EINTR
                return self.copy_from(source, target, timeout, recurse=2)
            if recurse == 2:
                return errno.EINTR
        if ret != 0:
            return errno.EIO
        return 0

    def get_path(self, path):
        storage = self.get_storage(path)
        if storage is None:
            raise ValueError("Could not find a MTP storage for path " + path)
        en = storage.find_entry(path)
        if not en is None and en.is_directory() and en.must_refresh:
            en.refresh()
        return en

    def remove_path(self, path):
        storage = self.get_storage(path)
        if storage is None:
            self.log.error('Could not find a MTP storage for path ' + path)
            return None
        return storage.remove_entry(path)

    # Create a dummy zero length file in the cache
    def create(self, path):
        dirpath, name = os.path.split(path)
        folderid = storageid = -1
        newfile = None
        if dirpath.strip == '':
            direntry = self.get_storage(path)
            storageid = direntry.get_id()
            folderid = 0
        else:
            direntry = self.get_path(dirpath)
            storageid = direntry.get_storage_id()
            folderid = direntry.get_id()
        if not direntry is None:
            newfile = MTPFile(id=-9999, path=path, storageid=storageid, folderid=folderid,
                              dt=time.time(), length=0)
            direntry.add_file(newfile)
        return newfile

    def copy_to(self, source: str, target: str, timeout=None, timestamp=None, recurse=0, retry=0):
        timeouterr = [0, ]

        def timeout_handler(sig, frame):
            timeouterr[0] = errno.EINTR

        direntry, entry, dirpath, name = self.__entry_and_dir(target)
        if entry is None:
            if not direntry is None and not direntry.is_directory():
                raise NotADirectoryError("Target directory does not exist")
        else:
            if entry.is_directory():
                return errno.EISDIR
            if entry.get_id() >= 0:
                err = self.libmtp.LIBMTP_Delete_Object(self.open_device.device, entry.get_id())
                if err != 0:
                    self.log.error("Delete object %d (%s) failed" % (entry.get_id(), entry.get_path()))
                else:
                    if not direntry is None:
                        direntry.must_refresh = True
                        direntry.refresh()
        fh = -1
        if type(source) == str or type(source) == unicode:
            if not os.path.exists(source):
                raise FileNotFoundError("Source file is not found")
            pfile = self.__new_filet(direntry, entry, name=name, localpath=source, timestamp=timestamp)
        else:
            try:
                fh = int(source)
                pfile = self.__new_filet(direntry, entry, name=name, handle=fh, timestamp=timestamp)
            except:
                self.log.error(
                    "mtp.copy_to(source, target): source must be a string (local path) or a integer (file handle)")
                return errno.EINVAL
        if bool(pfile):
            try:
                pfile[0].item_id = 0
                self.libmtp.LIBMTP_Clear_Errorstack(self.open_device.device)
                oldhandler = None
                if not timeout is None:
                    oldhandler = signal.signal(signal.SIGALRM, timeout_handler)
                    signal.alarm(timeout)
                try:
                    if fh >= 0:
                        os.lseek(fh, 0, os.SEEK_SET)
                        err = self.libmtp.LIBMTP_Send_File_From_File_Descriptor(self.open_device.device, fh, pfile,
                                                                                MTP.PROGRESS_FUNC_P(), c_void_p())
                    else:
                        err = self.libmtp.LIBMTP_Send_File_From_File(self.open_device.device,
                                                                     c_char_p(bytes(source, "utf8")), pfile,
                                                                     MTP.PROGRESS_FUNC_P(), c_void_p())
                    signal.alarm(0)
                finally:
                    if not oldhandler is None:
                        signal.signal(signal.SIGALRM, oldhandler)
                if timeouterr[0] != 0 or err != 0:
                    if timeout:
                        print("Error transferring %s to %s in %d seconds" % (str(source), str(target), timeout))
                    else:
                        print("Error transferring %s to %s" % (str(source), str(target)))
                    self.libmtp.LIBMTP_Dump_Errorstack(self.open_device.device)
                    if not self.check():
                        devno = "%04x:%04x" % (self.open_device.vendor_id, self.open_device.product_id)
                        if recurse == 0:
                            #                  self.log.warn('Resetting device ' +  devno)
                            #                  ret = self.libmtp.LIBMTP_Reset_Device(self.open_device.device)
                            self.close()
                            if self.open(devno, must_refresh=False):
                                return self.copy_to(source, target, timeout, timestamp, recurse=1)
                            else:
                                recurse = 1
                        if recurse == 1:
                            self.log.error('Reset device failed. Attempting to reopen')
                            self.close()
                            if not self.open(devno, must_refresh=True):
                                self.log.error("Could not reopen device.")
                                return errno.EINTR
                            return self.copy_to(source, target, timeout, timestamp, recurse=2)
                        if recurse == 2:
                            return errno.EINTR
                direntry.must_refresh = True
                direntry.refresh()
                return 0
            finally:
                self.__delete_filet(pfile)
        return errno.EIO

    def mkdir(self, path, recurse=0):
        direntry, entry, _, name = self.__entry_and_dir(path)
        storage = self.get_storage(path)
        if not entry is None:
            return False
        if not direntry is None and not direntry.is_directory():
            return False
        parentid = direntry.get_id() if not direntry is None else 0
        storageid = direntry.get_storage_id() if not direntry is None else storage.get_id() if not storage is None else 0
        self.libmtp.LIBMTP_Clear_Errorstack(self.open_device.device)
        newid = self.libmtp.LIBMTP_Create_Folder(self.open_device.device, c_char_p(name),
                                                 c_uint32(parentid), c_uint32(storageid))
        if newid <= 0:
            sys.stderr.write(path + ': ')
            self.libmtp.LIBMTP_Dump_Errorstack(self.open_device.device)

            if not self.check():
                devno = "%04x:%04x" % (self.open_device.vendor_id, self.open_device.product_id)
                if recurse == 0:
                    self.close()
                    if self.open(devno, must_refresh=True):
                        return self.mkdir(path, recurse=1)
                if recurse == 1:
                    self.log.error('Could not reconnect to device')
                    self.close()
                    return False
            else:
                return False
        if not direntry is None:
            direntry.must_refresh = True
            direntry.refresh()
        return True

    def rmdir(self, path):
        direntry, entry, _, _ = self.__entry_and_dir(path)
        if entry is None or not entry.is_directory():
            return False
        self.last_error = self.libmtp.LIBMTP_Delete_Object(self.open_device.device, entry.get_id())
        if not direntry is None and direntry.is_directory():
            direntry.must_refresh = True
            direntry.refresh()
            self.remove_path(path)
        return self.last_error == 0

    def rm(self, entry):
        if type(entry) == str or type(entry) == unicode:
            entry = self.get_path(entry)
        if entry is None or entry.is_directory():
            return False
        parententry = self.get_path(os.path.split(entry.get_path())[0])
        self.last_error = self.libmtp.LIBMTP_Delete_Object(self.open_device.device, entry.get_id())
        if not parententry is None and parententry.is_directory():
            parententry.must_refresh = True
            self.remove_path(entry.get_path())
        return self.last_error == 0

    def rename(self, oldpath, newpath):
        oldentry = self.get_path(oldpath)
        if oldentry is None:
            return False
        dirpath, name = os.path.split(oldpath)
        olddirentry = self.get_path(dirpath)
        newname = os.path.split(newpath)[1]
        newentry = self.get_path(newpath)
        if not newentry is None is None and newentry.is_directory() and newentry.object_count() > 0:
            return False
        dirpath, name = os.path.split(newpath)
        newdirentry = self.get_path(dirpath)
        isok = False
        localbackup = None
        fh = -1
        try:
            if not newentry is None and not newentry.is_directory():
                name, ext = os.path.splitext(os.path.split(newpath)[1])
                (fh, localbackup) = tempfile.mkstemp(prefix=name, suffix=ext)
                self.libmtp.LIBMTP_Get_File_To_File_Descriptor(self.open_device.device, newentry.get_id(), fh,
                                                               MTP.PROGRESS_FUNC_P(), c_void_p())
                self.libmtp.LIBMTP_Delete_Object(self.open_device.device, newentry.get_id())
            err = -1
            if oldentry.is_directory():
                pfolder = self.__new_foldert(olddirentry, oldentry)
                try:
                    err = self.libmtp.LIBMTP_Set_Folder_Name(self.open_device.device, pfolder, c_char_p(newname))
                finally:
                    self.__delete_foldert(pfolder)
            else:
                pfile = self.__new_filet(olddirentry, oldentry)
                try:
                    pname = self.__malloc_string(name)  # POINTER(c_char)
                    if not pname is None and bool(pname):
                        pfile[0].name = cast(pname, c_char_p)
                    err = self.libmtp.LIBMTP_Set_File_Name(self.open_device.device, pfile, c_char_p(newname))
                finally:
                    self.__delete_filet(pfile)
            isok = (err == 0)
            self.last_error = err
        finally:
            try:
                if fh >= 0 and self.__close(fh):
                    fh = -1
                if not isok and not localbackup is None:
                    fh = os.open(localbackup, 'r')
                    if fh < 0:
                        return False
                    pfile = self.__new_filet(newdirentry, newentry, handle=fh)
                    try:
                        ret = self.libmtp.LIBMTP_Send_File_From_File_Descriptor(self.open_device.device, fh, pfile,
                                                                                MTP.PROGRESS_FUNC_P(), c_void_p())
                        isok = (ret == 0)
                    finally:
                        self.__delete_filet(pfile)
            finally:
                if fh >= 0:
                    self.__close(fh)
                if not localbackup is None:
                    os.remove(localbackup)
                if not olddirentry is None and olddirentry.is_directory():
                    olddirentry.must_refresh = True
                if not newdirentry is None and newdirentry.is_directory():
                    newdirentry.must_refresh = True
        return isok

    def get_dir_by_id(self, storageid, folderid):
        pfolders = None
        find_folders = self.libmtp.LIBMTP_Get_Folder_List_For_Storage
        find_folders.restype = POINTER(LIBMTP_folder_struct)
        find_folder = self.libmtp.LIBMTP_Find_Folder
        find_folder.restype = POINTER(LIBMTP_folder_struct)
        try:
            pfolders = find_folders(self.open_device.device, storageid)
            if bool(pfolders):
                pfolder = find_folder(pfolders, folderid)
                return pfolder[0].name
        finally:
            if bool(pfolders):
                self.libmtp.LIBMTP_destroy_folder_t(pfolders)

    def __str__(self):
        if not self.open_device is None:
            return self.open_device.__str__()
        return 'No Open Devices'

    def get_last_error(self):
        return self.last_error

    def __entry_and_dir(self, path):
        dirpath, name = os.path.split(path)
        entry = self.get_path(path)
        direntry = None
        if dirpath != '':
            direntry = self.get_path(dirpath)
        return (direntry, entry, dirpath, name)

    def __new_foldert(self, parententry=None, entry=None, name=None):
        newf = self.libmtp.LIBMTP_new_folder_t
        newf.restype = POINTER(LIBMTP_folder_struct)
        pfolder = newf()
        if not parententry is None:
            pfolder[0].parent_id = parententry.get_id()
        if not entry is None:
            pfolder[0].folder_id = entry.get_id()
        pfolder[
            0].storage_id = entry.get_storage_id() if not entry is None else parententry.get_storage_id() if not parententry is None else 0
        if name is None and not entry is None:
            name = entry.get_name()
        if not name is None and name.strip() != '':
            # Must use malloc at least for the case of LIBMTP_Set_Folder_Name because it frees name and strdups the new name
            pname = self.__malloc_string(name)  # POINTER(c_char)
            if not pname is None and bool(pname):
                pfolder.name = cast(pname, c_char_p)
        return pfolder

    def __new_filet(self, direntry=None, entry=None, handle=-1, localpath=None, name=None, timestamp=None):
        newf = self.libmtp.LIBMTP_new_file_t
        newf.restype = POINTER(LIBMTP_file_struct)
        pfile = newf()
        if timestamp is None:
            pfile[0].modificationdate = c_long(long(time.time()))
        else:
            pfile[0].modificationdate = c_long(long(timestamp))
        if name is None and not entry is None:
            name = entry.get_name()

        if entry is not None:
            pfile[0].storage_id = entry.get_storage_id()
        elif direntry is not None:
            pfile[0].storage_id = direntry.get_storage_id()
        else:
            raise ValueError("Either direntry or entry must be set")

        pfile[0].item_id = 0
        pfile[0].filetype = MTPType.LIBMTP_FILETYPE_UNKNOWN
        if not entry is None:
            pfile[0].item_id = entry.get_id()
            pfile[0].filetype = MTPType.filetype(name)
            if direntry is None:
                pfile[0].parent_id = entry.get_folder_id()
        elif not localpath is None:
            if name is None:
                pfile[0].filetype = MTPType.filetype(os.path.split(localpath)[1])
            else:
                pfile[0].filetype = MTPType.filetype(name)
        if not name is None:
            bname = bytes(name, 'utf8')
            pfile[0].name = cast(create_string_buffer(bname), c_char_p)

        if not direntry is None:
            pfile[0].parent_id = direntry.get_id()
        filelen = -1
        if handle >= 0:
            try:
                filelen = os.lseek(handle, 0, os.SEEK_END)
            except:
                traceback.print_exc(file=sys.stdout)
                self.log.exception("")
                filelen = -1
            finally:
                try:
                    os.lseek(handle, 0, os.SEEK_SET)
                except:
                    traceback.print_exc(file=sys.stdout)
                    self.log.exception("")
        elif not localpath is None:
            filelen = os.path.getsize(localpath)
        else:
            if not entry is None:
                filelen = entry.get_length()
            else:
                filelen = 0
        pfile[0].filesize = 0 if filelen < 0 else filelen
        return pfile

    def __delete_filet(self, pfile):
        if bool(pfile):
            pfile[0].name = c_char_p()  # LIBMTP_destroy_file_t assumes name was malloced and frees it so set to NULL
            self.libmtp.LIBMTP_destroy_file_t(pfile)

    def __delete_foldert(self, pfolder):
        if bool(pfolder):
            #         pfolder[0].name = c_char_p()
            self.libmtp.LIBMTP_destroy_folder_t(pfolder)

    def __utftostr(self, s):
        UTF8Writer = codecs.getwriter('utf8')
        strstream = StringIO()
        try:
            utfout = UTF8Writer(strstream)
            utfout.write(utf8(s))
            strstream.flush()
            return strstream.getvalue()
        finally:
            if not strstream is None:
                try:
                    strstream.close()
                except:
                    pass

                    # please tell me there's a better way to do this

    def __malloc_string(self, s):
        s = self.__utftostr(utf8(s))
        malloc = self.libc.malloc
        malloc.restype = POINTER(c_char)
        buf = create_string_buffer(s)
        length = len(s)
        ps = malloc(length + 1)
        if not bool(ps):
            return None
        self.libc.memset(ps, c_int(0), c_size_t(length + 1))  # in case its not Linux which does zero malloced memory
        for i in range(0, length):
            ps[i] = buf[i]
        return ps

    def __close(self, handle):
        try:
            os.close(handle)
            return True
        except:
            return False
