rm ../libmtpstubs.so
gcc -c -fpic libmtpstubs.c
if (test $? -eq 0)
then
   gcc -shared -o ../libmtpstubs.so libmtpstubs.o
fi
