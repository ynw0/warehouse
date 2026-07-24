(() => {
  const CAMERA_ROOT_ID = "warehouseAttachmentCamera";

  function makeFileName(prefix = "attachment-photo") {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    return `${prefix}-${stamp}.jpg`;
  }

  function removeCameraRoot() {
    document.getElementById(CAMERA_ROOT_ID)?.remove();
  }

  function normalizeCameraError(error) {
    const name = error?.name || "";
    const message = String(error?.message || "");
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return new Error("摄像头权限被拒绝，请在浏览器或客户端权限设置中允许使用摄像头");
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError" || /requested device not found/i.test(message)) {
      return new Error("未检测到可用摄像头，请确认电脑已连接摄像头、Windows 相机权限已开启，并且当前客户端能访问摄像头");
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return new Error("摄像头无法打开，可能被其他程序占用或驱动异常，请关闭占用摄像头的软件后重试");
    }
    if (name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") {
      return new Error("当前摄像头不支持请求的拍照参数，请重试或选择图片上传");
    }
    if (name === "SecurityError") {
      return new Error("当前页面安全策略禁止访问摄像头，请使用支持相机权限的客户端打开，或把服务器配置为 HTTPS");
    }
    return new Error(message || "摄像头打开失败，请选择图片上传");
  }

  async function openCameraStream() {
    const constraints = [
      {
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 }
        },
        audio: false
      },
      { video: true, audio: false }
    ];
    let lastError = null;
    for (const constraint of constraints) {
      try {
        return await navigator.mediaDevices.getUserMedia(constraint);
      } catch (error) {
        lastError = error;
        if (error?.name === "NotAllowedError" || error?.name === "PermissionDeniedError" || error?.name === "SecurityError") {
          break;
        }
      }
    }
    throw normalizeCameraError(lastError);
  }

  async function waitForVideoReady(video) {
    if (video.readyState >= 2 && video.videoWidth > 0) {
      return;
    }
    await new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("摄像头预览启动超时，请重试")), 8000);
      video.addEventListener("loadedmetadata", () => {
        window.clearTimeout(timer);
        resolve();
      }, { once: true });
    });
  }

  async function capture(options = {}) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器或客户端不支持直接拍照，请选择图片上传");
    }

    return new Promise((resolve, reject) => {
      removeCameraRoot();
      let stream = null;
      let snapshotBlob = null;
      let previewUrl = "";
      let closed = false;

      const root = document.createElement("div");
      root.id = CAMERA_ROOT_ID;
      root.className = "camera-capture-modal";
      root.innerHTML = `
        <div class="camera-capture-dialog" role="dialog" aria-modal="true" aria-label="${options.title || "拍照"}">
          <div class="camera-capture-head">
            <strong>${options.title || "拍照"}</strong>
            <button type="button" data-camera-close aria-label="关闭">×</button>
          </div>
          <div class="camera-capture-stage">
            <video autoplay playsinline muted></video>
            <canvas hidden></canvas>
            <img alt="拍照预览" hidden>
            <div class="camera-capture-status" data-camera-status>正在打开摄像头...</div>
          </div>
          <div class="camera-capture-actions">
            <button type="button" class="primary" data-camera-shot disabled>拍照</button>
            <button type="button" data-camera-retake hidden>重拍</button>
            <button type="button" class="good" data-camera-use hidden>使用照片</button>
            <button type="button" data-camera-cancel>取消</button>
          </div>
        </div>
      `;
      document.body.appendChild(root);

      const video = root.querySelector("video");
      const canvas = root.querySelector("canvas");
      const preview = root.querySelector("img");
      const status = root.querySelector("[data-camera-status]");
      const shotButton = root.querySelector("[data-camera-shot]");
      const retakeButton = root.querySelector("[data-camera-retake]");
      const useButton = root.querySelector("[data-camera-use]");

      function stop() {
        stream?.getTracks().forEach(track => track.stop());
        stream = null;
      }

      function cleanup() {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        stop();
        removeCameraRoot();
      }

      function close(value) {
        if (closed) return;
        closed = true;
        cleanup();
        resolve(value || null);
      }

      function fail(error) {
        if (closed) return;
        closed = true;
        cleanup();
        reject(normalizeCameraError(error));
      }

      function setStatus(text, isError = false) {
        status.textContent = text;
        status.hidden = false;
        status.classList.toggle("error", isError);
      }

      root.querySelector("[data-camera-close]").addEventListener("click", () => close(null));
      root.querySelector("[data-camera-cancel]").addEventListener("click", () => close(null));
      root.addEventListener("keydown", event => {
        if (event.key === "Escape") close(null);
      });

      shotButton.addEventListener("click", () => {
        if (!stream || video.hidden || video.videoWidth <= 0) {
          setStatus("摄像头仍在启动，请稍等", false);
          return;
        }
        const width = video.videoWidth || 1280;
        const height = video.videoHeight || 720;
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").drawImage(video, 0, 0, width, height);
        canvas.toBlob(blob => {
          if (!blob) {
            fail(new Error("拍照失败，请重试"));
            return;
          }
          snapshotBlob = blob;
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          previewUrl = URL.createObjectURL(blob);
          preview.src = previewUrl;
          preview.hidden = false;
          video.hidden = true;
          shotButton.hidden = true;
          retakeButton.hidden = false;
          useButton.hidden = false;
          status.hidden = true;
        }, "image/jpeg", 0.9);
      });

      retakeButton.addEventListener("click", () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = "";
        snapshotBlob = null;
        preview.removeAttribute("src");
        preview.hidden = true;
        video.hidden = false;
        shotButton.hidden = false;
        shotButton.disabled = false;
        retakeButton.hidden = true;
        useButton.hidden = true;
        status.hidden = true;
      });

      useButton.addEventListener("click", () => {
        if (!snapshotBlob) return;
        const file = new File([snapshotBlob], makeFileName(options.filePrefix), { type: "image/jpeg" });
        close(file);
      });

      (async () => {
        try {
          stream = await openCameraStream();
          if (closed) {
            stop();
            return;
          }
          video.srcObject = stream;
          await video.play();
          await waitForVideoReady(video);
          if (closed) {
            stop();
            return;
          }
          status.hidden = true;
          shotButton.disabled = false;
        } catch (error) {
          if (!closed) {
            setStatus(normalizeCameraError(error).message, true);
            window.setTimeout(() => fail(error), 300);
          }
        }
      })();
    });
  }

  window.WarehouseAttachmentCamera = { capture };
})();
