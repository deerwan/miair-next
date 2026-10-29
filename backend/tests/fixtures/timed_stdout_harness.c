#include <errno.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
enum { play_samples_are_untimed = 0, play_samples_are_timed = 1 };

/* Deliberately interrupt and fragment every record write. */
static ssize_t fragmented_write(int fd, const void *buf, size_t size) {
  static int count = 0;
  if (++count % 7 == 0) {
    errno = EINTR;
    return -1;
  }
  return write(fd, buf, size > 19 ? 19 : size);
}
#define write fragmented_write
#include "miair_stdout.h"
#undef write

int main(void) {
  unsigned char data[5000 * 4];
  memset(data, 0x53, sizeof(data));
  if (!miair_stdout_enabled()) {
    return miair_stdout_write(data, 2, 1, 1, 2, 4) < 0;
  }
  miair_stdout_start(48000, 0);
  if (miair_stdout_write(data, 5000, 1, 0xffffffff, 10000000000ULL, 4) < 0)
    return 1;
  miair_stdout_boundary();
  if (miair_stdout_write(data, 4, 0, 0, 0, 4) < 0)
    return 1;
  miair_stdout_boundary();
  return 0;
}
