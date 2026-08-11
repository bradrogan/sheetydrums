// A PlayerHandle backed by an HTMLAudioElement — used for drums-only playback
// of the isolated stem. Mirrors the YouTube handle's interface so the transport
// and sync layers are source-agnostic.
import { clampSeek, type PlayerHandle } from './playback';

export function createAudioPlayer(el: HTMLAudioElement, src: string): PlayerHandle {
  el.src = src;
  // Load metadata up front so duration is known and seeks land immediately —
  // otherwise the first source-switch seek (below) is issued before the element
  // is seekable and the browser silently drops it, restarting at 0:00.
  el.preload = 'metadata';

  let tickCb: (t: number) => void = () => {};
  let stateCb: (playing: boolean) => void = () => {};
  let rafId = 0;

  // A seek requested before the media is seekable (readyState < HAVE_METADATA)
  // can't be applied yet; stash it and apply once metadata arrives.
  let pendingSeek: number | null = null;
  const HAVE_METADATA = 1;
  const applyPendingSeek = (): void => {
    if (pendingSeek == null) return;
    el.currentTime = clampSeek(pendingSeek, el.duration);
    pendingSeek = null;
    tickCb(el.currentTime);
  };
  el.addEventListener('loadedmetadata', applyPendingSeek);

  const seekAbsolute = (seconds: number): void => {
    if (el.readyState >= HAVE_METADATA) {
      el.currentTime = clampSeek(seconds, el.duration);
      tickCb(el.currentTime);
    } else {
      pendingSeek = seconds; // applied on 'loadedmetadata'
      el.load(); // kick the metadata fetch so that event fires
    }
  };

  const startLoop = (): void => {
    if (rafId) return;
    const loop = (): void => {
      tickCb(el.currentTime);
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);
  };
  const stopLoop = (): void => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
  };

  const onPlay = (): void => {
    stateCb(true);
    startLoop();
  };
  const onPauseOrEnd = (): void => {
    stateCb(false);
    stopLoop();
    tickCb(el.currentTime); // settle the playhead
  };
  el.addEventListener('play', onPlay);
  el.addEventListener('pause', onPauseOrEnd);
  el.addEventListener('ended', onPauseOrEnd);

  return {
    play: () => void el.play(),
    pause: () => el.pause(),
    toggle: () => (el.paused ? void el.play() : el.pause()),
    seekTo: (seconds) => seekAbsolute(seconds),
    seekBy: (delta) => {
      // Base off the pending target if we haven't loaded yet, else the live time.
      const base = el.readyState >= HAVE_METADATA ? el.currentTime : pendingSeek ?? 0;
      seekAbsolute(base + delta);
    },
    getCurrentTime: () => el.currentTime,
    getDuration: () => el.duration,
    setVolume: (pct) => {
      el.volume = Math.max(0, Math.min(1, pct / 100));
    },
    isPlaying: () => !el.paused,
    onTick: (cb) => {
      tickCb = cb;
    },
    onStateChange: (cb) => {
      stateCb = cb;
    },
    destroy: () => {
      stopLoop();
      el.pause();
      el.removeEventListener('play', onPlay);
      el.removeEventListener('pause', onPauseOrEnd);
      el.removeEventListener('ended', onPauseOrEnd);
      el.removeEventListener('loadedmetadata', applyPendingSeek);
      el.removeAttribute('src');
      el.load();
    },
  };
}
