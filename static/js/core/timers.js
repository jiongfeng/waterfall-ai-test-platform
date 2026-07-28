function createTimerRuntime(host = window) {
  return {
    setInterval(callback, delay) {
      return host.setInterval(callback, delay);
    },
    clearInterval(timerId) {
      host.clearInterval(timerId);
    },
  };
}

function formatElapsedDuration(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (value) => String(value).padStart(2, "0");

  if (hours > 0) {
    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
  }

  return `${pad(minutes)}:${pad(seconds)}`;
}

window.createTimerRuntime = createTimerRuntime;
window.formatElapsedDuration = formatElapsedDuration;
