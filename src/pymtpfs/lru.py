from collections import OrderedDict

class LRU(object):
   def __init__(self, size):
      self.size = size
      self.map = OrderedDict()

   def __getitem__(self, k):
      v = self.map.pop(k)
      self.map[k] = v
      return v
   
   def __setitem__(self, k, v):
      if k in self.map:
         self.map.pop(k)
      elif len(self.map) >= self.size:
         self.map.popitem(last=False)
      self.map[k] = v

   def __delitem__(self, k):
      if k in self.map:
         v = self.map.pop(k)
         del v
         
   def get(self, k, d=None):
      if not k in self.map:
         return d
      return self.__getitem__(k)

   def has_key(self, k):
      return self.map.has_key(k)

   def pop(self, k):
      return self.map.pop(k)

   def __contains__(self, item):
      return item in self.map

   def __iter__(self):
      return iter(self.map)

   def __len__(self):
      return len(self.map)
   
   def __str__(self):
      return "LRUCache (size={size}, length={length}) {data}".\
             format(size=self.size, length=len(self.map), data=str(self.map))

