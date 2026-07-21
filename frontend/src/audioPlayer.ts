// A PlayerHandle backed by an HTMLAudioElement — used for drums-only playback
// of the isolated stem. Mirrors the YouTube handle's interface so the transport
// and sync layers are source-agnostic.
import { clampSeek, type PlayerHandle } from './playback';

export function createAudioPlayer(el: HTMLAudioElement, src: string): PlayerHandle {
  el.src = src;

  let tickCb: (t: number) => void = () => {};
  let stateCb: (playing: boolean) => void = () => {};
  let rafId = 0;

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
    seekTo: (seconds) => {
      el.currentTime = clampSeek(seconds, el.duration);
      tickCb(el.currentTime);
    },
    seekBy: (delta) => {
      el.currentTime = clampSeek(el.currentTime + delta, el.duration);
      tickCb(el.currentTime);
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
      el.removeAttribute('src');
      el.load();
    },
  };
}
