/* MiAir timed-stdout v1 extension, MIT licensed.
 * Included by Shairport Sync 5.5.2 audio_stdout.c (see stdout-timing.patch).
 * Opt-in only: MIAIR_TIMED_STDOUT=1. Ordinary stdout remains raw PCM.
 * Header: MAP2, frames/u32, RTP/u32, kind/u32, playtime/u64, epoch/u64,
 * all integers little-endian. playtime is Shairport's MONOTONIC_RAW ns,
 * already mapped from the sender's PTP anchor. kind: 0 untimed, 1 timed,
 * 2 boundary (no payload). PCM is configured by MiAir as 48k/S16LE/stereo.
 */
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

static pthread_mutex_t miair_stdout_mutex = PTHREAD_MUTEX_INITIALIZER;
static uint64_t miair_stdout_epoch = 0;

static int miair_stdout_enabled(void) {
  const char *value = getenv("MIAIR_TIMED_STDOUT");
  return value && strcmp(value, "1") == 0;
}

static void miair_le(unsigned char *dst, uint64_t value, unsigned int length) {
  for (unsigned int i = 0; i < length; i++) {
    dst[i] = value & 0xff;
    value >>= 8;
  }
}

static int miair_write_all(const void *buf, size_t size) {
  const unsigned char *cursor = buf;
  size_t sent = 0;
  while (sent < size) {
    ssize_t rc = write(STDOUT_FILENO, cursor + sent, size - sent);
    if (rc < 0 && errno == EINTR)
      continue;
    if (rc <= 0)
      return -1;
    sent += rc;
  }
  return 0;
}

static int miair_header(unsigned int frames, uint32_t rtp, unsigned int kind,
                        uint64_t playtime) {
  unsigned char header[32];
  memcpy(header, "MAP2", 4);
  miair_le(header + 4, frames, 4);
  miair_le(header + 8, rtp, 4);
  miair_le(header + 12, kind, 4);
  miair_le(header + 16, playtime, 8);
  miair_le(header + 24, miair_stdout_epoch, 8);
  return miair_write_all(header, sizeof(header));
}

static void miair_stdout_boundary(void) {
  if (!miair_stdout_enabled())
    return;
  int old_cancel_state;
  pthread_setcancelstate(PTHREAD_CANCEL_DISABLE, &old_cancel_state);
  pthread_mutex_lock(&miair_stdout_mutex);
  miair_stdout_epoch++;
  miair_header(0, 0, 2, 0);
  pthread_mutex_unlock(&miair_stdout_mutex);
  pthread_setcancelstate(old_cancel_state, NULL);
}

static void miair_stdout_start(__attribute__((unused)) int rate,
                               __attribute__((unused)) int format) {
  miair_stdout_boundary();
}

static int miair_stdout_write(void *buf, int samples, int sample_type,
                              uint32_t timestamp, uint64_t playtime,
                              unsigned int frame_bytes) {
  if (!miair_stdout_enabled())
    return write(STDOUT_FILENO, buf, samples * frame_bytes);
  if (samples < 0 || frame_bytes != 4) {
    errno = EINVAL;
    return -1;
  }
  int old_cancel_state;
  int result = samples * frame_bytes;
  pthread_setcancelstate(PTHREAD_CANCEL_DISABLE, &old_cancel_state);
  pthread_mutex_lock(&miair_stdout_mutex);
  for (unsigned int offset = 0; offset < (unsigned int)samples;) {
    unsigned int frames = (unsigned int)samples - offset;
    if (frames > 4096)
      frames = 4096;
    uint64_t deadline = sample_type == play_samples_are_timed
                            ? playtime + (uint64_t)offset * 1000000000 / 48000
                            : 0;
    if (miair_header(frames, timestamp, sample_type == play_samples_are_timed,
                     deadline) < 0 ||
        miair_write_all((unsigned char *)buf + offset * frame_bytes,
                        frames * frame_bytes) < 0) {
      result = -1;
      break;
    }
    offset += frames;
  }
  pthread_mutex_unlock(&miair_stdout_mutex);
  pthread_setcancelstate(old_cancel_state, NULL);
  return result;
}
