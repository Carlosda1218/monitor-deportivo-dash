/* Video upload via fetch — bypasses Dash WebSocket for large files */
(function () {
  var _fileInput = null;
  var _attached  = false;
  var MAX_VIDEO_MB = 300;
  var MAX_VIDEO_BYTES = MAX_VIDEO_MB * 1024 * 1024;
  var ALLOWED_EXTS = { mp4: true, mov: true, avi: true, mkv: true, webm: true, m4v: true };

  function getOrCreateInput() {
    if (_fileInput && document.body.contains(_fileInput)) return _fileInput;
    _fileInput = document.createElement('input');
    _fileInput.type   = 'file';
    _fileInput.accept = 'video/*';
    _fileInput.style.cssText = 'display:none;position:fixed;top:-9999px';
    _fileInput.addEventListener('change', function () {
      if (_fileInput.files && _fileInput.files[0]) {
        uploadFile(_fileInput.files[0]);
        _fileInput.value = '';
      }
    });
    document.body.appendChild(_fileInput);
    return _fileInput;
  }

  function uploadFile(file) {
    var errEl = document.getElementById('replay-upload-err');
    var ext = (file.name || '').split('.').pop().toLowerCase();
    if (!ALLOWED_EXTS[ext]) {
      if (errEl) errEl.textContent = 'Formato no soportado. Usa MP4, MOV, AVI, MKV, WebM o M4V.';
      return;
    }
    if (file.size > MAX_VIDEO_BYTES) {
      if (errEl) errEl.textContent = 'Video demasiado grande. Límite: ' + MAX_VIDEO_MB + ' MB.';
      return;
    }
    if (errEl) errEl.textContent = 'Subiendo "' + file.name + '"…';

    var formData = new FormData();
    formData.append('file', file);

    fetch('/upload-video', { method: 'POST', body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (errEl) errEl.textContent = '';
        if (data.error) {
          if (errEl) errEl.textContent = 'Error: ' + data.error;
          return;
        }
        if (window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props('replay-upload-result', { data: data });
        }
      })
      .catch(function (err) {
        if (errEl) errEl.textContent = 'Error al subir: ' + err.toString();
      });
  }

  function attachHandlers() {
    var dropzone = document.getElementById('replay-upload-dropzone');
    if (!dropzone || _attached) return;
    _attached = true;

    dropzone.addEventListener('click', function () {
      getOrCreateInput().click();
    });

    dropzone.addEventListener('dragover', function (e) {
      e.preventDefault();
      dropzone.classList.add('upload-zone--drag');
    });
    dropzone.addEventListener('dragleave', function () {
      dropzone.classList.remove('upload-zone--drag');
    });
    dropzone.addEventListener('drop', function (e) {
      e.preventDefault();
      dropzone.classList.remove('upload-zone--drag');
      var file = e.dataTransfer && e.dataTransfer.files[0];
      if (file) uploadFile(file);
    });
  }

  function poll() {
    attachHandlers();
    if (_attached) {
      /* Re-attach if the component is ever unmounted by Dash */
      var check = setInterval(function () {
        if (!document.getElementById('replay-upload-dropzone')) {
          _attached = false;
          clearInterval(check);
          setTimeout(poll, 800);
        }
      }, 2000);
      return;
    }
    setTimeout(poll, 800);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', poll);
  } else {
    poll();
  }
})();
