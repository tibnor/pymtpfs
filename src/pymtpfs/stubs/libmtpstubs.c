#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <libmtp.h>

static void hexdump(FILE *out, const char *buf, int n)
//------------------------------------------
{
   register int i, j, k;
   char ch;

   if (out == NULL)
      out = stdout;
   if ( (buf != NULL) && (n > 0) )
   {
      i = 0;
      while (i<n)
      {
         for (j=0; j<16; ++j)
         {
            k = i+j;
            if (k < n)
            {
               ch = buf[k];
               if ( (ch == '\n') || (ch == '\t') )
                  fprintf(out, "? ");
               else
               {
                  if (isprint(ch))
                     fprintf(out, "%c ", ch);
                  else
                     fprintf(out, "? ");
               }
            }
            else
               fprintf(out, "  ");
         }
         fprintf(out, "\t");
         for (j=0; j<16; ++j)
         {
            k = i+j;
            if (k >= n) break;
            fprintf(out, "%02X ", (int) buf[k]);
         }
         fprintf(out, "\n");
         i = k + 1;         
      }
   }
   else
      fprintf(out, "NULL\n");
}

int 
LIBMTP_Send_File_From_File_Descriptor(LIBMTP_mtpdevice_t *device, int const fd,
                                      LIBMTP_file_t *const filedata,
                                      LIBMTP_progressfunc_t const callback, void const *const data)
/*-----------------------------------------------------------------------------------------------*/
{
   char buf[32];
   int cb;
   FILE *out = NULL;

   out = fopen("/tmp/DEBUG.LOG", "a+");
   fprintf(out, "LIBMTP_Send_File_From_File_Descriptor %s: id=%d, parent=%d, storage=%d, size=%ld, type=%d (%d)\n",  filedata->filename, filedata->item_id,
           filedata->parent_id, filedata->storage_id, filedata->filesize, filedata->filetype, LIBMTP_FILETYPE_UNKNOWN);
//    while ((cb = read(fd, buf, 32)) > 0)
//       hexdump(out, buf, cb);
    fflush(out);
    fclose(out);
    return 0;  
}

int
LIBMTP_Send_File_From_File(LIBMTP_mtpdevice_t *  device, char const *const path, LIBMTP_file_t *const filedata,
                           LIBMTP_progressfunc_t const callback, void const *const data)
/*-----------------------------------------------------------------------------------------------*/
{
   char buf[32];
   int fd, cb, err;
   FILE *out = NULL;
   
   out = fopen("/tmp/DEBUG.LOG", "a+");
   fprintf(out, "LIBMTP_Send_File_From_File %s: id=%d, parent=%d, storage=%d, size=%ld, type=%d (%d) local path = %s\n",  filedata->filename, filedata->item_id,
           filedata->parent_id, filedata->storage_id, filedata->filesize, filedata->filetype, LIBMTP_FILETYPE_UNKNOWN, path);
   if ( (fd = open(path, O_RDONLY)) < 0)
   {
      err =  errno;
      fprintf(out, "Error opening local file %s (%d)", path, err);
      return -1;
   }
//   while ((cb = read(fd, buf, 32)) > 0)
//      hexdump(out, buf, cb);
   fflush(out);
   fclose(out);
   close(fd);
   return 0;
}
