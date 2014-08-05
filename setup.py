import os
from setuptools import setup, find_packages

def read(fname):
   return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
   name = "pymtpfs",
   version = "0.0.2",
   author = "Donald Munro",
   author_email = 'donaldmunro@gmail.com',
   description = ("A FUSE based filesystem for Media Transfer Protocol (MTP) devices implemented in Python."),
   license = "Apache 2",
   keywords = "FUSE filesystem MTP",
   url = "https://github.com/donaldmunro/pymtpfs",
   packages = find_packages("src", exclude="tests"),
   install_requires=["fusepy >= 2.0.1", ],
   long_description=read('README'),
   classifiers=[
      'Development Status :: 4 - Beta',
      'Environment :: Console',
      'Intended Audience :: Developers',
      'Operating System :: MacOS',
      'Operating System :: POSIX',
      'Operating System :: Unix',  
      "Development Status :: 3 - Alpha",
      "Topic :: System :: Filesystems",
      'License :: OSI Approved :: Apache Software License',
    ],
)
