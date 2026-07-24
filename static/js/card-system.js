    const state = {
      shelves: [],
      materials: [],
      currentShelf: null,
      currentMaterial: null,
      targetMaterialId: null,
      three: null,
      threeTargetRing: null
    };
    const THREE_RENDER_FPS = 30;
    const THREE_FRAME_INTERVAL = 1000 / THREE_RENDER_FPS;
    let threeVisibilityListenerAttached = false;
    const colors = ["#69a7ff", "#7dd6a6", "#f6c85f", "#f28c8c", "#bfa1ff", "#75d7d0"];

    document.addEventListener("DOMContentLoaded", () => {
      document.getElementById("findBtn")?.addEventListener("click", openSearchModal);
      document.getElementById("materialManageBtn")?.addEventListener("click", openMaterialManageModal);
      document.getElementById("exportBtn")?.addEventListener("click", openExportModal);
      document.getElementById("addShelfBtn")?.addEventListener("click", () => openShelfModal());
      document.getElementById("addMaterialBtn")?.addEventListener("click", () => openPlaceMaterialModal());
      document.getElementById("closeViewer")?.addEventListener("click", closeViewer);
      document.getElementById("spotlight").addEventListener("click", event => {
        if (event.target.id === "spotlight") event.currentTarget.classList.remove("open");
      });
      ensureThreeVisibilityListener();
      loadBootstrap();
    });

    async function api(url, options = {}) {
      const response = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      if (!response.ok) {
        let message = "请求失败";
        try {
          const data = await response.json();
          message = data.error || message;
        } catch (error) {}
        throw new Error(message);
      }
      const type = response.headers.get("content-type") || "";
      return type.includes("application/json") ? response.json() : response.text();
    }

    async function loadBootstrap() {
      const data = await api("/api/bootstrap");
      state.shelves = data.shelves || [];
      state.materials = data.materials || [];
      const dbLabel = document.getElementById("dbLabel");
      if (dbLabel) {
        dbLabel.textContent = data.db_file ? `SQLite: ${data.db_file}` : "SQLite 物料编码数据库";
      }
      renderHome();
    }

    function renderHome() {
      const home = document.getElementById("home");
      if (window.renderSystemDashboard) {
        window.renderSystemDashboard(home);
        return;
      }
      home.innerHTML = "";
      home.appendChild(renderWarehouseBand("office", "办公用品库"));
      home.appendChild(renderWarehouseBand("rd", "研发材料库"));
      if (state.targetMaterialId) markTarget(state.targetMaterialId);
    }

    function renderWarehouseBand(type, title) {
      const shelves = state.shelves.filter(shelf => shelf.warehouse_type === type);
      const band = document.createElement("section");
      band.className = "warehouse-band";
      band.innerHTML = `
        <div class="band-head">
          <div>
            <h2>${title}</h2>
            <span>${shelves.length} 个货架，首页自动排列</span>
          </div>
          <button data-add="${type}">添加货架</button>
        </div>
        <div class="floor"></div>
      `;
      band.querySelector("[data-add]").addEventListener("click", () => openShelfModal(null, type));
      const floor = band.querySelector(".floor");
      shelves.forEach(shelf => {
        const tile = renderShelfTile(shelf, false, true);
        tile.addEventListener("click", () => openViewer(shelf.id));
        floor.appendChild(tile);
      });
      return band;
    }

    function sortedLayersForDisplay(shelf) {
      return [...(shelf.layers || [])].sort((a, b) => Number(b.layer_number) - Number(a.layer_number));
    }

    function materialPhotoUrl(material) {
      if (!material) return "";
      if (material.material_photo_url) return material.material_photo_url;
      const batches = material.batches || [];
      for (const batch of batches) {
        if (Number(batch.quantity || 0) <= 0) continue;
        const photo = (batch.attachments || []).find(item => item.is_image && (item.attachment_type === "material_photo" || item.attachment_type === "photo"));
        if (photo?.download_url) return photo.download_url;
      }
      return "";
    }

    function materialPhotoHtml(material, className = "material-card-photo") {
      const url = materialPhotoUrl(material);
      if (!url) return "";
      return `<a class="${className}" href="${escapeAttr(url)}" target="_blank" rel="noopener"><img src="${escapeAttr(url)}" alt="${escapeAttr(material.name || material.material_code || "物料照片")}"></a>`;
    }

    function renderShelfTile(shelf, large, withDelete = false) {
      const tile = document.createElement("article");
      tile.className = "shelf-tile";
      tile.dataset.shelfId = shelf.id;
      const materials = state.materials.filter(material => material.shelf_id === shelf.id);
      tile.innerHTML = `
        ${withDelete ? `<button type="button" class="shelf-delete-badge" data-delete-shelf title="\u5220\u9664\u8d27\u67b6" aria-label="\u5220\u9664\u8d27\u67b6">&times;</button>` : ""}
        <div class="shelf-title">
          <span>${escapeHtml(shelf.name)}</span>
          <small>${materials.length} 个物料</small>
        </div>
        <div class="flat-shelf">
          ${sortedLayersForDisplay(shelf).map(layer => `
            <div class="flat-layer" style="grid-template-columns: repeat(${Math.max(layer.zones.length, 1)}, minmax(0, 1fr));">
              ${layer.zones.map(zone => renderFlatZone(shelf, layer, zone, large)).join("")}
            </div>
          `).join("")}
        </div>
      `;
      tile.querySelector("[data-delete-shelf]")?.addEventListener("click", async event => {
        event.preventDefault();
        event.stopPropagation();
        await deleteShelfWithConfirm(shelf);
      });
      return tile;
    }

    async function deleteShelfWithConfirm(shelf) {
      if (!confirm(`\u786e\u8ba4\u5220\u9664\u8d27\u67b6\u201c${shelf.name}\u201d\uff1f\u7269\u6599\u4e3b\u6570\u636e\u4f1a\u4fdd\u7559\uff0c\u4f46\u8be5\u8d27\u67b6\u4e0a\u7684\u5b58\u653e\u4f4d\u7f6e\u4f1a\u5220\u9664\u3002`)) return;
      try {
        await api(`/api/shelves/${shelf.id}`, { method: "DELETE" });
        const wasViewing = state.currentShelf?.id === shelf.id;
        await loadBootstrap();
        if (wasViewing) closeViewer();
        toast("\u8d27\u67b6\u5df2\u5220\u9664");
      } catch (error) {
        toast(error.message || "\u5220\u9664\u8d27\u67b6\u5931\u8d25");
      }
    }

    function renderFlatZone(shelf, layer, zone, large) {
      const materials = state.materials.filter(material =>
        material.shelf_id === shelf.id &&
        Number(material.layer_number) === Number(layer.layer_number) &&
        material.zone_name === zone.name
      );
      const chips = large ? `<div class="zone-chips">${materials.map(material => {
        const photoUrl = materialPhotoUrl(material);
        return `<button class="material-chip ${photoUrl ? "has-photo" : ""} ${state.targetMaterialId === material.id ? "is-target" : ""}" data-material-id="${material.id}">
          ${photoUrl ? `<span class="material-chip-photo" style="background-image:url('${escapeAttr(photoUrl)}')"></span>` : `<span class="material-chip-icon">${escapeHtml(material.icon || "□")}</span>`}
          <span>${escapeHtml(material.material_code)}</span>
        </button>`;
      }).join("")}</div>` : "";
      return `
        <div class="flat-zone" data-zone-key="${shelf.id}-${layer.layer_number}-${zone.name}" style="background:${escapeHtml(zone.color || "#eef4fb")}55">
          <span class="zone-note">${layer.layer_number} 层 ${escapeHtml(zone.name)} 区 · ${escapeHtml(zone.note || "未注释")} · ${materials.length}</span>
          ${chips}
        </div>
      `;
    }

    async function openViewer(shelfId, targetMaterialId) {
      const shelf = await api(`/api/shelves/${shelfId}`);
      state.currentShelf = shelf;
      state.currentMaterial = null;
      state.targetMaterialId = targetMaterialId || state.targetMaterialId;
      document.getElementById("viewerTitle").textContent = shelf.name;
      document.getElementById("viewerSub").textContent = `${shelf.warehouse_type === "office" ? "办公用品库" : "研发材料库"} · ${shelf.layers.length} 层`;
      document.getElementById("viewer").classList.add("open");
      renderSidePanel();
      setupThree(shelf);
    }

    function closeViewer() {
      document.getElementById("viewer").classList.remove("open");
      disposeThree();
      state.currentShelf = null;
      state.currentMaterial = null;
      renderHome();
    }

    function renderSidePanel(material = null) {
      const panel = document.getElementById("sidePanel");
      const selected = material || state.currentMaterial;
      panel.innerHTML = `
        <div class="panel-tools">
          <button id="panelEditShelf">调整货架</button>
          <button id="panelAddMaterial" class="primary">添加物料</button>
          <button id="panelMoveMaterial">调整物料</button>
        </div>
        <div id="panelContent"></div>
      `;
      document.getElementById("panelEditShelf").addEventListener("click", () => openShelfModal(state.currentShelf));
      document.getElementById("panelAddMaterial").addEventListener("click", () => openPlaceMaterialModal(state.currentShelf?.id));
      document.getElementById("panelMoveMaterial").addEventListener("click", () => openPlaceMaterialModal(state.currentShelf?.id, state.currentMaterial, true));
      const content = document.getElementById("panelContent");
      if (!selected) {
        content.innerHTML = `<div class="empty-state">点击货架上的物料查看料卡。左键拖动旋转，滚轮缩放，按住鼠标中键拖动可上下左右平移。</div>`;
        return;
      }
      content.innerHTML = `
        <div class="material-card">
          ${materialPhotoHtml(selected)}
          <h3>${escapeHtml(selected.icon || "□")} ${escapeHtml(selected.name)}</h3>
          <div class="facts">
            <div class="fact"><span>物料编号</span><strong>${escapeHtml(selected.material_code)}</strong></div>
            <div class="fact"><span>品牌型号</span><span>${escapeHtml(selected.brand_model || "")}</span></div>
            <div class="fact"><span>技术规格</span><span>${escapeHtml(selected.spec || "")}</span></div>
            <div class="fact"><span>存放位置</span><span>${escapeHtml(selected.shelf_name || "")} / ${selected.layer_number || "-"} 层 / ${escapeHtml(selected.zone_name || "")} 区</span></div>
            <div class="fact"><span>当前余量</span><strong>${formatQty(selected.quantity)} ${escapeHtml(selected.unit || "")}</strong></div>
          </div>
          <h4>批次库存</h4>
          <table class="records">
            <thead><tr><th>批次</th><th>入库日期</th><th>数量</th><th>库龄</th></tr></thead>
            <tbody>
              ${(selected.batches || []).map(batch => `
                <tr>
                  <td>${escapeHtml(batch.batch_no || "")}</td>
                  <td>${escapeHtml(batch.received_date || "")}</td>
                  <td>${formatQty(batch.quantity)} ${escapeHtml(selected.unit || "")}</td>
                  <td>${Number(batch.age_days || 0)} 天</td>
                </tr>
              `).join("") || `<tr><td colspan="4">暂无批次库存</td></tr>`}
            </tbody>
          </table>
          <div class="stock-actions">
            <button class="good" id="stockInBtn">入库</button>
            <button class="warn" id="stockOutBtn">出库</button>
          </div>
          ${stockRecordsTable(selected.records, ["in", "out"], "入库 / 出库记录", "暂无入库 / 出库记录")}
          ${stockRecordsTable(selected.records, ["borrow", "return"], "借用 / 归还记录", "暂无借用 / 归还记录")}
        </div>
      `;
      document.getElementById("stockInBtn").addEventListener("click", () => openStockModal(selected, "in"));
      document.getElementById("stockOutBtn").addEventListener("click", () => openStockModal(selected, "out"));
    }

    async function showMaterialCard(materialId) {
      const material = await api(`/api/materials/${materialId}`);
      state.currentMaterial = material;
      renderSidePanel(material);
    }

    function showZoneMaterials(zoneInfo) {
      state.currentMaterial = null;
      const panel = document.getElementById("sidePanel");
      const zoneMaterials = state.materials.filter(material =>
        material.shelf_id === zoneInfo.shelfId &&
        Number(material.layer_number) === Number(zoneInfo.layerNumber) &&
        material.zone_name === zoneInfo.zoneName
      );
      panel.innerHTML = `
        <div class="panel-tools">
          <button id="panelEditShelf">调整货架</button>
          <button id="panelAddMaterial" class="primary">添加物料</button>
          <button id="panelMoveMaterial">调整物料</button>
        </div>
        <div id="panelContent">
          <div class="material-card">
            <h3>${escapeHtml(zoneInfo.layerNumber)} 层 ${escapeHtml(zoneInfo.zoneName)} 区</h3>
            <div class="facts">
              <div class="fact"><span>货架</span><strong>${escapeHtml(state.currentShelf?.name || "")}</strong></div>
              <div class="fact"><span>注释</span><span>${escapeHtml(zoneInfo.note || "未注释")}</span></div>
              <div class="fact"><span>物料数</span><strong>${zoneMaterials.length}</strong></div>
            </div>
            <div class="search-results">
              ${zoneMaterials.map(material => `
                <div class="result-row ${materialPhotoUrl(material) ? "with-photo" : ""}">
                  ${materialPhotoHtml(material, "result-photo-link")}
                  <div>
                    <strong>${escapeHtml(material.icon || "□")} ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)}</strong>
                    <small>${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")} · 余量 ${formatQty(material.quantity)} ${escapeHtml(material.unit || "")}</small>
                  </div>
                  <button data-zone-material="${material.id}">料卡</button>
                </div>
              `).join("") || `<div class="empty-state">该区域暂无物料</div>`}
            </div>
          </div>
        </div>
      `;
      document.getElementById("panelEditShelf").addEventListener("click", () => openShelfModal(state.currentShelf));
      document.getElementById("panelAddMaterial").addEventListener("click", () => openPlaceMaterialModal(state.currentShelf?.id));
      document.getElementById("panelMoveMaterial").addEventListener("click", () => openPlaceMaterialModal(state.currentShelf?.id, null, true));
      panel.querySelectorAll("[data-zone-material]").forEach(button => {
        button.addEventListener("click", () => showMaterialCard(Number(button.dataset.zoneMaterial)));
      });
    }

    function setupThree(shelf) {
      disposeThree();
      const host = document.getElementById("threeHost");
      const width = host.clientWidth || 900;
      const height = host.clientHeight || 650;
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0xeef2f6);
      const camera = new THREE.PerspectiveCamera(46, width / height, .1, 1000);
      const layerCount = Math.max(shelf.layers.length, 1);
      camera.position.set(0, 3.8, Math.max(12, layerCount * 2.6 + 8));
      camera.lookAt(0, 0, 0);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(width, height);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      host.appendChild(renderer.domElement);

      const group = new THREE.Group();
      group.position.set(0, 0, 0);
      scene.add(group);
      scene.add(new THREE.AmbientLight(0xffffff, .78));
      const light = new THREE.DirectionalLight(0xffffff, .95);
      light.position.set(4, 7, 6);
      scene.add(light);

      const baseMaterial = new THREE.MeshStandardMaterial({ color: 0x7a5237, roughness: .64 });
      const postMaterial = new THREE.MeshStandardMaterial({ color: 0x563725, roughness: .72 });
      const zoneMaterial = new THREE.MeshStandardMaterial({ color: 0xc9d8e8, roughness: .6, transparent: true, opacity: .62 });
      const shelfWidth = 7.2;
      const shelfDepth = 2.6;
      const layerGap = 1.45;
      const bottomY = -((layerCount - 1) * layerGap) / 2;
      const clickables = [];
      const layers = [...(shelf.layers || [])].sort((a, b) => Number(a.layer_number) - Number(b.layer_number));

      for (let i = 0; i < 4; i++) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(.16, layerCount * layerGap + .9, .16), postMaterial);
        post.position.set(i < 2 ? -shelfWidth / 2 : shelfWidth / 2, 0, i % 2 ? -shelfDepth / 2 : shelfDepth / 2);
        group.add(post);
      }

      layers.forEach((layer, layerIndex) => {
        const y = bottomY + layerIndex * layerGap;
        const board = new THREE.Mesh(new THREE.BoxGeometry(shelfWidth + .45, .16, shelfDepth + .24), baseMaterial);
        board.position.y = y - .16;
        group.add(board);
        const zoneWidth = shelfWidth / Math.max(layer.zones.length, 1);
        layer.zones.forEach((zone, zoneIndex) => {
          const x = -shelfWidth / 2 + zoneWidth / 2 + zoneIndex * zoneWidth;
          const area = new THREE.Mesh(new THREE.BoxGeometry(zoneWidth - .12, .08, shelfDepth - .24), zoneMaterial.clone());
          area.position.set(x, y + .01, 0);
          area.userData.zoneInfo = {
            shelfId: shelf.id,
            layerNumber: layer.layer_number,
            zoneName: zone.name,
            note: zone.note || ""
          };
          group.add(area);
          clickables.push(area);
          const label = makeLabel(`${layer.layer_number}${zone.name} ${zone.note || ""}`);
          label.position.set(x, y + .28, shelfDepth / 2 + .08);
          group.add(label);

          const materials = state.materials.filter(material =>
            material.shelf_id === shelf.id &&
            Number(material.layer_number) === Number(layer.layer_number) &&
            material.zone_name === zone.name
          ).sort((a, b) => Number(a.slot_index || 0) - Number(b.slot_index || 0) || String(a.material_code || "").localeCompare(String(b.material_code || "")));
          const layout = zoneMaterialLayout(materials.length, zoneWidth, shelfDepth);
          materials.forEach((material, materialIndex) => {
            const col = materialIndex % layout.cols;
            const row = Math.floor(materialIndex / layout.cols);
            const localX = -layout.innerW / 2 + layout.cellX * (col + .5);
            const localZ = -layout.innerD / 2 + layout.cellZ * (row + .5);
            const model = createMaterialModel(material, layout.size, material.id === state.targetMaterialId ? 0xffcf33 : materialColor(material.id));
            model.position.set(x + localX, y + .18 + layout.size * .46, localZ);
            model.userData.materialId = material.id;
            group.add(model);
            registerClickableModel(model, material.id, clickables);
            if (materialPhotoUrl(material)) {
              addMaterialPhotoSprite(group, material, model.position.x, y + .48 + layout.size * 1.78, model.position.z, layout.size, clickables);
            }
            if (layout.size >= .18 || material.id === state.targetMaterialId) {
              const chip = makeLabel(`${material.icon || "□"} ${material.material_code}`, layout.labelScale);
              chip.position.set(model.position.x, y + .34 + layout.size * 1.35, model.position.z);
              group.add(chip);
            }
            if (material.id === state.targetMaterialId) {
              const ring = new THREE.Mesh(
                new THREE.TorusGeometry(Math.max(.2, layout.size * 1.35), Math.max(.018, layout.size * .11), 12, 48),
                new THREE.MeshBasicMaterial({ color: 0xffd447 })
              );
              ring.position.copy(model.position);
              ring.rotation.x = Math.PI / 2;
              group.add(ring);
              state.threeTargetRing = ring;
            }
          });
        });
      });

      const raycaster = new THREE.Raycaster();
      const pointer = new THREE.Vector2();
      let dragMode = null;
      let lastX = 0;
      let lastY = 0;
      renderer.domElement.addEventListener("contextmenu", event => event.preventDefault());
      renderer.domElement.addEventListener("pointerdown", event => {
        event.preventDefault();
        dragMode = event.button === 1 ? "pan" : "rotate";
        lastX = event.clientX;
        lastY = event.clientY;
        renderer.domElement.setPointerCapture(event.pointerId);
      });
      renderer.domElement.addEventListener("pointerup", event => {
        dragMode = null;
        try { renderer.domElement.releasePointerCapture(event.pointerId); } catch (error) {}
      });
      renderer.domElement.addEventListener("pointermove", event => {
        if (!dragMode) return;
        const dx = event.clientX - lastX;
        const dy = event.clientY - lastY;
        if (dragMode === "pan") {
          group.position.x += dx * .012;
          group.position.y -= dy * .012;
        } else {
          group.rotation.y += dx * .008;
          group.rotation.x += dy * .004;
          group.rotation.x = Math.max(-.55, Math.min(.55, group.rotation.x));
        }
        lastX = event.clientX;
        lastY = event.clientY;
      });
      renderer.domElement.addEventListener("wheel", event => {
        event.preventDefault();
        camera.position.z = Math.max(5, Math.min(18, camera.position.z + event.deltaY * .01));
      }, { passive: false });
      renderer.domElement.addEventListener("click", event => {
        const rect = renderer.domElement.getBoundingClientRect();
        pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(pointer, camera);
        const hit = raycaster.intersectObjects(clickables, true)[0];
        const materialHit = objectWithUserData(hit?.object, "materialId");
        const zoneHit = objectWithUserData(hit?.object, "zoneInfo");
        if (materialHit) {
          showMaterialCard(materialHit.userData.materialId);
        } else if (zoneHit) {
          showZoneMaterials(zoneHit.userData.zoneInfo);
        }
      });

      const three = {
        renderer,
        scene,
        frame: null,
        animate: null,
        resize: null,
        resizeTimer: null,
        lastFrameTime: 0
      };
      const animate = timestamp => {
        if (state.three !== three || document.hidden || !isViewerOpen()) {
          three.frame = null;
          return;
        }
        three.frame = requestAnimationFrame(animate);
        if (timestamp - three.lastFrameTime < THREE_FRAME_INTERVAL) return;
        three.lastFrameTime = timestamp;
        if (state.threeTargetRing) state.threeTargetRing.scale.setScalar(1 + Math.sin(Date.now() / 240) * .12);
        renderer.render(scene, camera);
      };
      const resize = () => {
        if (three.resizeTimer !== null) window.clearTimeout(three.resizeTimer);
        three.resizeTimer = window.setTimeout(() => {
          three.resizeTimer = null;
          if (state.three !== three) return;
          const nextWidth = host.clientWidth || width;
          const nextHeight = host.clientHeight || height;
          camera.aspect = nextWidth / nextHeight;
          camera.updateProjectionMatrix();
          renderer.setSize(nextWidth, nextHeight);
        }, 200);
      };
      three.animate = animate;
      three.resize = resize;
      window.addEventListener("resize", resize);
      state.three = three;
      startThreeAnimation();
      if (state.targetMaterialId) showMaterialCard(state.targetMaterialId);
    }

    function addMaterialPhotoSprite(parent, material, x, y, z, size, clickables) {
      const url = materialPhotoUrl(material);
      if (!url) return;
      const loader = new THREE.TextureLoader();
      loader.load(url, texture => {
        if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
        const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }));
        const aspect = texture.image?.width && texture.image?.height ? texture.image.width / texture.image.height : 1;
        const height = Math.max(.32, Math.min(.56, size * 1.7));
        sprite.scale.set(Math.max(.32, Math.min(.72, height * aspect)), height, 1);
        sprite.position.set(x, y, z);
        sprite.userData.materialId = material.id;
        parent.add(sprite);
        clickables.push(sprite);
      });
    }

    function makeLabel(text, scale = 1) {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 72;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "rgba(255,255,255,.92)";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = "#d9e0ea";
      ctx.strokeRect(.5, .5, canvas.width - 1, canvas.height - 1);
      ctx.fillStyle = "#172033";
      ctx.font = "24px Microsoft YaHei, Arial";
      ctx.textBaseline = "middle";
      ctx.fillText(text.slice(0, 20), 14, 36);
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas) }));
      sprite.scale.set(1.7 * scale, .38 * scale, 1);
      return sprite;
    }

    function isViewerOpen() {
      return Boolean(document.getElementById("viewer")?.classList.contains("open"));
    }

    function startThreeAnimation() {
      const three = state.three;
      if (!three || document.hidden || !isViewerOpen() || three.frame !== null) return;
      three.lastFrameTime = 0;
      three.frame = requestAnimationFrame(three.animate);
    }

    function stopThreeAnimation(three = state.three) {
      if (!three || three.frame === null) return;
      cancelAnimationFrame(three.frame);
      three.frame = null;
    }

    function ensureThreeVisibilityListener() {
      if (threeVisibilityListenerAttached) return;
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
          stopThreeAnimation();
        } else {
          startThreeAnimation();
        }
      });
      threeVisibilityListenerAttached = true;
    }

    function disposeThree() {
      if (!state.three) return;
      const three = state.three;
      stopThreeAnimation(three);
      if (three.resizeTimer !== null) window.clearTimeout(three.resizeTimer);
      window.removeEventListener("resize", three.resize);
      if (three.scene) {
        three.scene.traverse(object => {
          if (object.geometry) object.geometry.dispose();
          const materials = Array.isArray(object.material) ? object.material : (object.material ? [object.material] : []);
          materials.forEach(material => {
            if (material.map) material.map.dispose();
            if (material.dispose) material.dispose();
          });
        });
      }
      document.getElementById("threeHost").innerHTML = "";
      three.renderer.dispose();
      state.three = null;
      state.threeTargetRing = null;
    }

    function materialColor(id) {
      return [0x2675d9, 0x1d9a68, 0xb9790f, 0xc84b4b, 0x7b61c9, 0x258b95][id % 6];
    }

    function zoneMaterialLayout(count, zoneWidth, shelfDepth) {
      const innerW = Math.max(.34, zoneWidth - .34);
      const innerD = Math.max(.44, shelfDepth - .56);
      if (!count) return { cols: 1, rows: 1, cellX: innerW, cellZ: innerD, innerW, innerD, size: .32, labelScale: .85 };
      let cols = Math.max(1, Math.min(count, Math.ceil(Math.sqrt(count * innerW / innerD))));
      const comfortRows = Math.max(1, Math.floor(innerD / .42));
      cols = Math.max(cols, Math.ceil(count / comfortRows));
      cols = Math.min(count, cols);
      const rows = Math.ceil(count / cols);
      const cellX = innerW / cols;
      const cellZ = innerD / rows;
      const size = Math.max(.11, Math.min(.34, Math.min(cellX, cellZ) * .56));
      const labelScale = Math.max(.45, Math.min(.85, size / .32));
      return { cols, rows, cellX, cellZ, innerW, innerD, size, labelScale };
    }

    function materialModelKind(material) {
      const text = materialSearchText(material);
      const rules = [
        ["fpc_connector", /fpc连接器|ffc连接器|翻盖式|抽屉式|zif|连接座/],
        ["fpc_cable", /fpc|ffc|排线|柔性线路|连接排线|软排线|同向|反向|异面|同面/],
        ["smd_resistor", /贴片电阻|电阻|resistor/],
        ["smd_capacitor", /贴片电容|电容|capacitor|钽电容|陶瓷电容/],
        ["inductor", /电感|磁珠|共模|绕线|inductor|bead/],
        ["crystal", /晶振|有源晶振|无源晶振|oscillator|crystal/],
        ["diode_led", /二极管|发光二极管|led|肖特基|稳压管|保险丝|fuse|tvs/],
        ["transistor", /三极管|场效应管|mos|mosfet|igbt|功率电子开关|电机驱动/],
        ["switch_button", /开关|按钮|按键|拨码|switch|button/],
        ["ic_chip", /芯片|集成|接口芯片|专用芯片|电平转换|存储|flash|ddr|cpu|mcu|soc|fpga|imu|cmos|传感器芯片|\bic\b/],
        ["sensor_lens", /镜头|cmos传感器|光源|摄像|相机|lens|camera/],
        ["glasses_device", /ai眼镜|眼镜|智能眼镜/],
        ["dev_board", /开发板|分电板|pcb|电路板|板卡|模块|核心板|主板|jlink|下载器|hba卡|网卡/],
        ["cable_coil", /线缆|电缆|电源线|电池线|连接线|转接线|调试线|射频电缆|高清线|vga|hdmi|dp转|usb延长|线材|wire|cable/],
        ["power_supply", /电源(?!线)|充电器|适配器|隔离电源|开关电源|power/],
        ["battery", /电池(?!线)|锂电池|纽扣电池|cr\d+|battery|充电套装/],
        ["connector", /连接器|接插件|端子|插座|插头|排针|排母|usb|type-a|type-c|xt30|接口|connector/],
        ["antenna", /天线|wifi天线|射频天线|antenna/],
        ["motor", /电机|舵机|风扇|泵|马达|motor/],
        ["heat_sink", /散热|散热器|导热|heatsink/],
        ["structure_panel", /结构手板|结构件|连接件|转接件|手板|胶壳|外壳|吊舱|桨叶|桨保|头盔|面板|支架|框架|结构/],
        ["fastener", /螺丝|螺钉|螺母|螺柱|紧固件|金属紧固|screw|bolt|nut/],
        ["spacer_washer", /垫片|尼龙柱|减震柱|硅胶螺丝垫片|垫圈|柱|washer|spacer/],
        ["rubber_seal", /橡胶|硅胶|氟橡胶|线圈|密封圈|t型塞|堵头|塞/],
        ["protection_foam", /防护|泡棉|海绵|防尘|保护膜|膜|缓冲/],
        ["tape_roll", /胶带|地贴|双面胶|美纹|封箱胶|tape/],
        ["glue_tube", /胶水|液体胶|研发用胶|膏体|点胶|针筒|针头|油漆|腻子|除胶剂|硅脂/],
        ["tool", /工具|螺丝刀|剪刀|美工刀|剥线钳|卷笔刀|搬运工具|推车|梯|角阀|锁|tool/],
        ["paper_stack", /打印纸|办公用纸|生活用纸|纸巾|抽纸|擦手纸|标签纸|贴纸|信封|纸制品|paper/],
        ["card_badge", /门禁卡|ic卡|卡套|身份|证件|胸牌/],
        ["notebook_folder", /笔记本|文件夹|文件袋|档案袋|资料盒|文件筐|板夹|收纳|标识牌|桌牌/],
        ["pen_marker", /笔|记号笔|中性笔|白板笔|荧光笔|铅笔|马克笔|笔芯|laser pointer|激光笔/],
        ["office_clip", /订书|回形针|长尾.*夹|推夹|号码印|印章|橡皮|扎带|票夹|clip/],
        ["it_device", /显示器|键盘|鼠标|键鼠|u盘|光盘|硬盘|内存条|存储卡|打印机|投影仪|读卡器|转接器|交换机|遥控器|计算器|软件|信息化/],
        ["toner_cartridge", /墨粉|碳粉|硒鼓|粉盒|墨盒|印油|cartridge|toner/],
        ["cleaning_bottle", /洗洁精|洗手液|洁厕|消毒|碘伏|药品|感冒灵|板蓝根|创可贴|清洁|香薰|除味|化肥/],
        ["bin_bag", /垃圾袋|垃圾桶|沥水桶|水桶|纸巾盒|投票箱|工具箱|箱|桶|盒/],
        ["cloth_flag", /抹布|桌布|横幅|党旗|红旗|工衣|挂绳|布制品|伞|雨伞/],
        ["food_pack", /咖啡豆|茶叶|食品|饮品|零食/],
        ["bottle_cylinder", /保温壶|饮用水|抽水器|液|瓶|罐|卷/]
      ];
      const hit = rules.find(([, pattern]) => pattern.test(text));
      return hit ? hit[0] : "storage_box";
    }

    function materialSearchText(material) {
      return `${material.icon || ""} ${material.material_code || ""} ${material.name || ""} ${material.brand_model || ""} ${material.spec || ""} ${material.category || ""} ${material.sub_category || ""} ${material.category_name || ""} ${material.material_type || ""}`.toLowerCase();
    }

    function meshMaterial(color, options = {}) {
      return new THREE.MeshStandardMaterial({
        color,
        roughness: options.roughness ?? .48,
        metalness: options.metalness ?? .04,
        transparent: Boolean(options.opacity && options.opacity < 1),
        opacity: options.opacity ?? 1
      });
    }

    function addMesh(group, geometry, material, position = [0, 0, 0], rotation = [0, 0, 0]) {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(position[0], position[1], position[2]);
      mesh.rotation.set(rotation[0], rotation[1], rotation[2]);
      group.add(mesh);
      return mesh;
    }

    function addPinRow(group, count, size, metal, z, y = -size * .04) {
      for (let i = 0; i < count; i++) {
        const px = (i - (count - 1) / 2) * size * .2;
        addMesh(group, new THREE.BoxGeometry(size * .055, size * .07, size * .24), metal, [px, y, z]);
      }
    }

    function addTerminalPair(group, size, metal) {
      addMesh(group, new THREE.BoxGeometry(size * .22, size * .08, size * .54), metal, [size * .5, -size * .02, 0]);
      addMesh(group, new THREE.BoxGeometry(size * .22, size * .08, size * .54), metal, [-size * .5, -size * .02, 0]);
    }

    function addBoardDetails(group, size, dark, metal) {
      addMesh(group, new THREE.BoxGeometry(size * .34, size * .13, size * .26), dark, [-size * .3, size * .13, 0]);
      addMesh(group, new THREE.BoxGeometry(size * .2, size * .11, size * .2), metal, [size * .22, size * .12, size * .22]);
      addMesh(group, new THREE.BoxGeometry(size * .16, size * .1, size * .2), metal, [size * .45, size * .11, -size * .18]);
    }

    function addCableEnd(group, size, metal, x, z = 0) {
      addMesh(group, new THREE.BoxGeometry(size * .22, size * .16, size * .28), metal, [x, 0, z]);
    }

    function createMaterialModel(material, size, color) {
      const group = new THREE.Group();
      const base = meshMaterial(color);
      const dark = meshMaterial(0x1f2937, { roughness: .56 });
      const metal = meshMaterial(0xb9c2cc, { roughness: .3, metalness: .45 });
      const green = meshMaterial(0x1d8b5a, { roughness: .52 });
      const yellow = meshMaterial(0xd7a51f, { roughness: .5 });
      const paper = meshMaterial(0xf4f0df, { roughness: .72 });
      const white = meshMaterial(0xf7fafc, { roughness: .62 });
      const rubber = meshMaterial(0x2d3036, { roughness: .82 });
      const glass = meshMaterial(0x7ec8e3, { roughness: .16, metalness: .08, opacity: .62 });
      const kind = materialModelKind(material);
      if (kind === "ic_chip") {
        addMesh(group, new THREE.BoxGeometry(size * 1.28, size * .22, size * .92), dark);
        addPinRow(group, 6, size, metal, size * .58);
        addPinRow(group, 6, size, metal, -size * .58);
        addMesh(group, new THREE.CircleGeometry(size * .07, 16), metal, [-size * .42, size * .13, size * .27], [-Math.PI / 2, 0, 0]);
      } else if (kind === "smd_resistor") {
        addMesh(group, new THREE.BoxGeometry(size * .76, size * .14, size * .42), meshMaterial(0x3d4048, { roughness: .7 }));
        addTerminalPair(group, size, metal);
      } else if (kind === "smd_capacitor") {
        addMesh(group, new THREE.BoxGeometry(size * .62, size * .22, size * .44), meshMaterial(0xd5b16b, { roughness: .58 }));
        addTerminalPair(group, size, metal);
      } else if (kind === "inductor") {
        addMesh(group, new THREE.BoxGeometry(size * .74, size * .3, size * .62), dark);
        addMesh(group, new THREE.TorusGeometry(size * .24, size * .05, 8, 28), metal, [0, size * .12, 0], [Math.PI / 2, 0, 0]);
      } else if (kind === "crystal") {
        addMesh(group, new THREE.CylinderGeometry(size * .22, size * .22, size * .82, 24), metal, [0, 0, 0], [0, 0, Math.PI / 2]);
        addMesh(group, new THREE.BoxGeometry(size * .82, size * .05, size * .34), metal, [0, -size * .2, 0]);
      } else if (kind === "diode_led") {
        addMesh(group, new THREE.CylinderGeometry(size * .17, size * .17, size * .66, 18), dark, [0, 0, 0], [0, 0, Math.PI / 2]);
        addMesh(group, new THREE.BoxGeometry(size * .08, size * .22, size * .36), metal, [size * .18, 0, 0]);
        addMesh(group, new THREE.SphereGeometry(size * .18, 16, 10), glass, [-size * .22, 0, 0]);
      } else if (kind === "transistor") {
        addMesh(group, new THREE.BoxGeometry(size * .72, size * .32, size * .58), dark);
        for (let i = 0; i < 3; i++) addMesh(group, new THREE.BoxGeometry(size * .07, size * .34, size * .07), metal, [(i - 1) * size * .19, -size * .32, size * .16]);
        addMesh(group, new THREE.BoxGeometry(size * .42, size * .04, size * .62), metal, [0, size * .2, 0]);
      } else if (kind === "switch_button") {
        addMesh(group, new THREE.BoxGeometry(size * .86, size * .2, size * .58), dark);
        addMesh(group, new THREE.CylinderGeometry(size * .2, size * .2, size * .16, 24), base, [0, size * .2, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .12, size * .26, size * .08), metal, [-size * .28, -size * .22, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .12, size * .26, size * .08), metal, [size * .28, -size * .22, 0]);
      } else if (kind === "connector" || kind === "fpc_connector") {
        addMesh(group, new THREE.BoxGeometry(size * 1.32, size * .42, size * .62), base);
        addPinRow(group, kind === "fpc_connector" ? 9 : 5, size, metal, size * .42, -size * .22);
        if (kind === "fpc_connector") addMesh(group, new THREE.BoxGeometry(size * 1.08, size * .08, size * .16), dark, [0, size * .25, -size * .22]);
      } else if (kind === "fpc_cable") {
        addMesh(group, new THREE.BoxGeometry(size * 1.45, size * .045, size * .46), yellow);
        for (let i = 0; i < 5; i++) addMesh(group, new THREE.BoxGeometry(size * 1.28, size * .018, size * .018), metal, [0, size * .04, (i - 2) * size * .08]);
        addMesh(group, new THREE.BoxGeometry(size * .16, size * .08, size * .5), metal, [size * .72, size * .04, 0]);
      } else if (kind === "cable_coil") {
        addMesh(group, new THREE.TorusGeometry(size * .38, size * .055, 10, 40), base, [0, 0, 0], [Math.PI / 2, 0, 0]);
        addCableEnd(group, size, metal, size * .42);
        addCableEnd(group, size, metal, -size * .42);
      } else if (kind === "dev_board") {
        addMesh(group, new THREE.BoxGeometry(size * 1.42, size * .11, size * .9), green);
        addBoardDetails(group, size, dark, metal);
        for (let i = 0; i < 4; i++) addMesh(group, new THREE.CylinderGeometry(size * .035, size * .035, size * .04, 10), metal, [(i < 2 ? -1 : 1) * size * .58, size * .09, (i % 2 ? -1 : 1) * size * .34]);
      } else if (kind === "sensor_lens") {
        addMesh(group, new THREE.BoxGeometry(size * 1.02, size * .12, size * .75), green);
        addMesh(group, new THREE.CylinderGeometry(size * .25, size * .28, size * .32, 28), dark, [0, size * .2, 0]);
        addMesh(group, new THREE.SphereGeometry(size * .2, 20, 10), glass, [0, size * .38, 0]);
      } else if (kind === "glasses_device") {
        addMesh(group, new THREE.TorusGeometry(size * .22, size * .035, 8, 24), dark, [-size * .28, 0, 0], [0, Math.PI / 2, 0]);
        addMesh(group, new THREE.TorusGeometry(size * .22, size * .035, 8, 24), dark, [size * .28, 0, 0], [0, Math.PI / 2, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .2, size * .045, size * .08), metal, [0, 0, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .035, size * .035, size * .66, 8), dark, [-size * .55, -size * .02, -size * .2], [Math.PI / 2, .18, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .035, size * .035, size * .66, 8), dark, [size * .55, -size * .02, -size * .2], [Math.PI / 2, -.18, 0]);
      } else if (kind === "power_supply") {
        addMesh(group, new THREE.BoxGeometry(size * 1.08, size * .42, size * .72), dark);
        addMesh(group, new THREE.CylinderGeometry(size * .04, size * .04, size * .72, 8), rubber, [size * .66, 0, 0], [0, 0, Math.PI / 2]);
        addCableEnd(group, size, metal, size * .94);
      } else if (kind === "battery") {
        if (/纽扣|coin|cr\d+/i.test(materialSearchText(material))) {
          addMesh(group, new THREE.CylinderGeometry(size * .34, size * .34, size * .14, 32), metal, [0, 0, 0]);
          addMesh(group, new THREE.CylinderGeometry(size * .24, size * .24, size * .03, 32), white, [0, size * .08, 0]);
        } else {
          addMesh(group, new THREE.CylinderGeometry(size * .22, size * .22, size * .95, 24), metal, [0, 0, 0], [0, 0, Math.PI / 2]);
          addMesh(group, new THREE.CylinderGeometry(size * .23, size * .23, size * .12, 24), dark, [size * .48, 0, 0], [0, 0, Math.PI / 2]);
        }
      } else if (kind === "antenna") {
        addMesh(group, new THREE.CylinderGeometry(size * .045, size * .045, size * .92, 10), dark, [0, size * .16, 0], [0, 0, -.55]);
        addMesh(group, new THREE.BoxGeometry(size * .42, size * .14, size * .32), base, [-size * .22, -size * .22, 0]);
      } else if (kind === "motor") {
        addMesh(group, new THREE.CylinderGeometry(size * .3, size * .3, size * .55, 28), metal, [0, 0, 0], [Math.PI / 2, 0, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .05, size * .05, size * .48, 12), dark, [0, 0, size * .42], [Math.PI / 2, 0, 0]);
      } else if (kind === "heat_sink") {
        addMesh(group, new THREE.BoxGeometry(size * .95, size * .12, size * .68), metal, [0, -size * .16, 0]);
        for (let i = 0; i < 5; i++) addMesh(group, new THREE.BoxGeometry(size * .09, size * .48, size * .68), metal, [(i - 2) * size * .18, size * .06, 0]);
      } else if (kind === "structure_panel") {
        addMesh(group, new THREE.BoxGeometry(size * 1.18, size * .16, size * .82), base);
        addMesh(group, new THREE.BoxGeometry(size * .32, size * .32, size * .82), base, [-size * .43, size * .2, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .08, size * .08, size * .2, 16), dark, [size * .36, size * .11, size * .22]);
        addMesh(group, new THREE.CylinderGeometry(size * .08, size * .08, size * .2, 16), dark, [size * .36, size * .11, -size * .22]);
      } else if (kind === "fastener") {
        addMesh(group, new THREE.CylinderGeometry(size * .22, size * .22, size * .52, 24), metal, [0, -size * .04, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .36, size * .36, size * .14, 6), metal, [0, size * .28, 0]);
      } else if (kind === "spacer_washer") {
        addMesh(group, new THREE.TorusGeometry(size * .3, size * .075, 12, 28), metal, [0, 0, 0], [Math.PI / 2, 0, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .08, size * .08, size * .55, 18), metal, [size * .42, 0, 0]);
      } else if (kind === "rubber_seal") {
        addMesh(group, new THREE.TorusGeometry(size * .34, size * .07, 12, 36), rubber, [0, 0, 0], [Math.PI / 2, 0, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .18, size * .26, size * .32, 18), rubber, [size * .5, 0, 0], [0, 0, Math.PI / 2]);
      } else if (kind === "protection_foam") {
        addMesh(group, new THREE.BoxGeometry(size * 1.08, size * .28, size * .78), meshMaterial(0xd9e3ef, { roughness: .86, opacity: .76 }));
        addMesh(group, new THREE.BoxGeometry(size * .86, size * .06, size * .56), white, [0, size * .2, 0]);
      } else if (kind === "tape_roll") {
        addMesh(group, new THREE.TorusGeometry(size * .32, size * .12, 16, 36), base, [0, 0, 0], [Math.PI / 2, 0, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .12, size * .12, size * .08, 20), paper, [0, 0, 0], [Math.PI / 2, 0, 0]);
      } else if (kind === "glue_tube") {
        addMesh(group, new THREE.CylinderGeometry(size * .16, size * .22, size * .86, 18), base, [0, 0, 0], [0, 0, Math.PI / 2]);
        addMesh(group, new THREE.ConeGeometry(size * .12, size * .24, 18), metal, [size * .54, 0, 0], [0, 0, -Math.PI / 2]);
      } else if (kind === "tool") {
        addMesh(group, new THREE.CylinderGeometry(size * .11, size * .13, size * .72, 16), base, [-size * .18, 0, 0], [0, 0, Math.PI / 2]);
        addMesh(group, new THREE.BoxGeometry(size * .62, size * .08, size * .12), metal, [size * .32, 0, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .18, size * .04, size * .36), metal, [size * .67, 0, 0]);
      } else if (kind === "paper_stack") {
        for (let i = 0; i < 4; i++) addMesh(group, new THREE.BoxGeometry(size * .92, size * .035, size * .66), paper, [0, i * size * .045, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .92, size * .015, size * .66), white, [size * .025, size * .19, -size * .025]);
      } else if (kind === "notebook_folder") {
        addMesh(group, new THREE.BoxGeometry(size * .9, size * .12, size * .72), base);
        addMesh(group, new THREE.BoxGeometry(size * .08, size * .18, size * .72), dark, [-size * .46, size * .04, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .46, size * .035, size * .2), paper, [size * .12, size * .1, size * .12]);
      } else if (kind === "pen_marker") {
        addMesh(group, new THREE.CylinderGeometry(size * .07, size * .07, size * 1.08, 14), base, [0, 0, 0], [0, 0, Math.PI / 2]);
        addMesh(group, new THREE.ConeGeometry(size * .08, size * .18, 14), metal, [size * .61, 0, 0], [0, 0, -Math.PI / 2]);
        addMesh(group, new THREE.CylinderGeometry(size * .08, size * .08, size * .18, 14), dark, [-size * .58, 0, 0], [0, 0, Math.PI / 2]);
      } else if (kind === "office_clip") {
        addMesh(group, new THREE.BoxGeometry(size * .62, size * .12, size * .42), metal);
        addMesh(group, new THREE.BoxGeometry(size * .52, size * .06, size * .12), dark, [0, size * .12, size * .28]);
        addMesh(group, new THREE.TorusGeometry(size * .22, size * .035, 8, 28), metal, [0, size * .05, -size * .22], [Math.PI / 2, 0, 0]);
      } else if (kind === "it_device") {
        addMesh(group, new THREE.BoxGeometry(size * 1.14, size * .12, size * .68), dark);
        addMesh(group, new THREE.BoxGeometry(size * .96, size * .055, size * .48), meshMaterial(0x304b7a, { roughness: .38 }), [0, size * .09, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .18, size * .2, size * .28), metal, [size * .55, 0, 0]);
      } else if (kind === "toner_cartridge") {
        addMesh(group, new THREE.BoxGeometry(size * 1.06, size * .34, size * .5), dark);
        addMesh(group, new THREE.CylinderGeometry(size * .11, size * .11, size * 1.0, 20), metal, [0, -size * .22, 0], [0, 0, Math.PI / 2]);
      } else if (kind === "cleaning_bottle") {
        addMesh(group, new THREE.CylinderGeometry(size * .23, size * .28, size * .62, 20), base, [0, 0, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .13, size * .13, size * .18, 16), white, [0, size * .4, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .16, size * .1, size * .28), metal, [size * .17, size * .5, 0]);
      } else if (kind === "bin_bag") {
        addMesh(group, new THREE.BoxGeometry(size * .76, size * .7, size * .58), base);
        addMesh(group, new THREE.BoxGeometry(size * .62, size * .08, size * .44), dark, [0, size * .39, 0]);
        addMesh(group, new THREE.CylinderGeometry(size * .18, size * .18, size * .18, 18), metal, [0, size * .48, 0]);
      } else if (kind === "cloth_flag") {
        addMesh(group, new THREE.BoxGeometry(size * .1, size * .78, size * .08), metal, [-size * .42, size * .08, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .72, size * .34, size * .05), base, [0, size * .28, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .64, size * .06, size * .48), meshMaterial(0xd6dbe4, { roughness: .9 }), [0, -size * .18, 0]);
      } else if (kind === "card_badge") {
        addMesh(group, new THREE.BoxGeometry(size * .72, size * .08, size * .5), base);
        addMesh(group, new THREE.BoxGeometry(size * .5, size * .03, size * .24), white, [0, size * .07, 0]);
        addMesh(group, new THREE.TorusGeometry(size * .16, size * .02, 8, 24), metal, [0, size * .12, size * .34], [Math.PI / 2, 0, 0]);
      } else if (kind === "food_pack") {
        addMesh(group, new THREE.BoxGeometry(size * .62, size * .66, size * .34), base);
        addMesh(group, new THREE.BoxGeometry(size * .68, size * .08, size * .38), metal, [0, size * .38, 0]);
        addMesh(group, new THREE.BoxGeometry(size * .42, size * .2, size * .03), paper, [0, size * .05, size * .19]);
      } else if (kind === "bottle_cylinder") {
        addMesh(group, new THREE.CylinderGeometry(size * .28, size * .28, size * .72, 24), base, [0, 0, 0], [0, 0, Math.PI / 2]);
        addMesh(group, new THREE.CylinderGeometry(size * .18, size * .18, size * .12, 20), metal, [size * .42, 0, 0], [0, 0, Math.PI / 2]);
      } else {
        addMesh(group, new THREE.BoxGeometry(size * .92, size * .58, size * .72), base);
        addMesh(group, new THREE.BoxGeometry(size * .78, size * .06, size * .58), dark, [0, size * .32, 0]);
      }
      return group;
    }

    function registerClickableModel(model, materialId, clickables) {
      model.userData.materialId = materialId;
      model.traverse(child => {
        if (child.isMesh) {
          child.userData.materialId = materialId;
          clickables.push(child);
        }
      });
    }

    function objectWithUserData(object, key) {
      let node = object;
      while (node) {
        if (node.userData && node.userData[key]) return node;
        node = node.parent;
      }
      return null;
    }

    function openShelfModal(shelf = null, defaultType = "office") {
      const isEdit = Boolean(shelf);
      openModal({
        title: isEdit ? "调整货架" : "添加货架",
        body: `
          <div class="form-grid">
            <label>货架名称<input id="shelfName" value="${escapeAttr(shelf?.name || "")}" placeholder="例如 研发三号架"></label>
            <label>所属库区
              <select id="shelfType">
                <option value="office" ${(shelf?.warehouse_type || defaultType) === "office" ? "selected" : ""}>办公用品库</option>
                <option value="rd" ${(shelf?.warehouse_type || defaultType) === "rd" ? "selected" : ""}>研发材料库</option>
              </select>
            </label>
            <label>层数<input id="layerCount" type="number" min="1" max="12" value="${shelf?.layers?.length || 2}"></label>
            <label>默认每层分区数<input id="defaultZoneCount" type="number" min="1" max="12" value="${shelf?.layers?.[0]?.zones?.length || 3}"></label>
            <div class="wide" id="layerEditors"></div>
            ${isEdit ? `<button type="button" id="deleteShelfBtn" class="danger wide">删除货架</button>` : ""}
          </div>
        `,
        okText: "保存货架",
        onReady: () => {
          const render = () => renderLayerEditors(shelf);
          document.getElementById("layerCount").addEventListener("input", render);
          document.getElementById("defaultZoneCount").addEventListener("input", render);
          render();
          const deleteBtn = document.getElementById("deleteShelfBtn");
          deleteBtn?.addEventListener("click", async () => {
            if (!confirm("确认删除该货架？物料主数据会保留，但货架上的存放位置会删除。")) return;
            try {
              await api(`/api/shelves/${shelf.id}`, { method: "DELETE" });
              const wasViewing = state.currentShelf?.id === shelf.id;
              await loadBootstrap();
              closeModal();
              if (wasViewing) closeViewer();
              toast("货架已删除");
            } catch (error) {
              toast(error.message || "删除货架失败");
            }
          });
        },
        onOk: async () => {
          const payload = {
            name: document.getElementById("shelfName").value.trim() || "未命名货架",
            warehouse_type: document.getElementById("shelfType").value,
            shape: "straight",
            position_x: 0,
            position_y: 0,
            layers: collectLayerEditors()
          };
          if (isEdit) {
            await api(`/api/shelves/${shelf.id}`, { method: "PUT", body: JSON.stringify(payload) });
          } else {
            await api("/api/shelves", { method: "POST", body: JSON.stringify(payload) });
          }
          await loadBootstrap();
          if (state.currentShelf) await openViewer(state.currentShelf.id);
          toast("货架已保存");
        }
      });
    }

    function renderLayerEditors(existingShelf) {
      const host = document.getElementById("layerEditors");
      const layerCount = clampInt(document.getElementById("layerCount").value, 1, 12);
      const defaultZoneCount = clampInt(document.getElementById("defaultZoneCount").value, 1, 12);
      host.innerHTML = "";
      for (let i = 1; i <= layerCount; i += 1) {
        const oldLayer = existingShelf?.layers?.find(layer => Number(layer.layer_number) === i);
        const zoneCount = oldLayer?.zones?.length || defaultZoneCount;
        const box = document.createElement("div");
        box.className = "layer-editor";
        box.dataset.layerNumber = i;
        box.innerHTML = `
          <label>第 ${i} 层分区数
            <input class="zone-count" type="number" min="1" max="12" value="${zoneCount}">
          </label>
          <div class="zone-list"></div>
        `;
        host.appendChild(box);
        const renderZones = () => renderZoneEditors(box, oldLayer);
        box.querySelector(".zone-count").addEventListener("input", renderZones);
        renderZones();
      }
    }

    function renderZoneEditors(layerBox, oldLayer) {
      const list = layerBox.querySelector(".zone-list");
      const count = clampInt(layerBox.querySelector(".zone-count").value, 1, 12);
      list.innerHTML = "";
      for (let i = 0; i < count; i += 1) {
        const oldZone = oldLayer?.zones?.[i];
        const row = document.createElement("div");
        row.className = "zone-editor";
        row.innerHTML = `
          <input class="zone-name" value="${escapeAttr(oldZone?.name || String.fromCharCode(65 + i))}">
          <input class="zone-note" value="${escapeAttr(oldZone?.note || "")}" placeholder="区域注释，例如 R01 电阻">
        `;
        list.appendChild(row);
      }
    }

    function collectLayerEditors() {
      return [...document.querySelectorAll(".layer-editor")].map(layerBox => ({
        layer_number: Number(layerBox.dataset.layerNumber),
        zones: [...layerBox.querySelectorAll(".zone-editor")].map((row, index) => ({
          name: (row.querySelector(".zone-name").value || String.fromCharCode(65 + index)).trim().toUpperCase(),
          note: row.querySelector(".zone-note").value.trim(),
          capacity: 10,
          color: colors[index % colors.length]
        }))
      }));
    }

    function openPlaceMaterialModal(defaultShelfId = null, existingMaterial = null, isAdjust = false) {
      const shelfId = defaultShelfId || existingMaterial?.shelf_id || state.shelves[0]?.id || "";
      let selectedMaterial = existingMaterial || null;
      openModal({
        title: isAdjust || existingMaterial ? "调整物料" : "添加物料",
        body: `
          <div class="form-grid">
            <label class="wide">搜索已有物料
              <input id="materialPickSearch" placeholder="输入物料编号 / 物料名称 / 品牌型号 / 技术规格" value="${escapeAttr(existingMaterial?.material_code || "")}">
            </label>
            <div class="wide">
              <button id="runMaterialPick" class="primary">搜索选择</button>
              <span class="hint" id="selectedMaterialText">${existingMaterial ? `已选择：${escapeHtml(existingMaterial.material_code)} · ${escapeHtml(existingMaterial.name)}` : "请选择数据库已有物料"}</span>
            </div>
            <div class="wide search-results" id="materialPickResults"></div>
            <label>货架<select id="matShelf"></select></label>
            <label>层<select id="matLayer"></select></label>
            <label>区域<select id="matZone"></select></label>
          </div>
        `,
        okText: isAdjust || existingMaterial ? "保存调整" : "添加到货架",
        onReady: () => {
          const shelfSelect = document.getElementById("matShelf");
          shelfSelect.innerHTML = state.shelves.map(shelf => `<option value="${shelf.id}" ${Number(shelf.id) === Number(shelfId) ? "selected" : ""}>${escapeHtml(shelf.name)}</option>`).join("");
          shelfSelect.addEventListener("change", updateLocationSelects);
          document.getElementById("matLayer").addEventListener("change", updateZoneSelect);
          updateLocationSelects(existingMaterial);
          document.getElementById("runMaterialPick").addEventListener("click", runMaterialPickDropdownSearch);
          document.getElementById("materialPickSearch").addEventListener("keydown", event => {
            if (event.key === "Enter") runMaterialPickDropdownSearch();
          });
          if (existingMaterial) runMaterialPickDropdownSearch();
        },
        onOk: async () => {
          if (!selectedMaterial) throw new Error("请先搜索并选择数据库已有物料");
          await api(`/api/materials/${selectedMaterial.id}/position`, {
            method: "PUT",
            body: JSON.stringify({
              shelf_id: Number(document.getElementById("matShelf").value),
              layer_number: Number(document.getElementById("matLayer").value),
              zone_name: document.getElementById("matZone").value
            })
          });
          await loadBootstrap();
          if (state.currentShelf) await openViewer(Number(document.getElementById("matShelf").value), selectedMaterial.id);
          toast(isAdjust || existingMaterial ? "物料位置已调整" : "物料已添加到货架");
        }
      });

      async function runMaterialPickDropdownSearch() {
        const keyword = document.getElementById("materialPickSearch").value.trim();
        const results = keyword ? await api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`) : [];
        const host = document.getElementById("materialPickResults");
        if (!results.length) {
          host.innerHTML = `<div class="empty-state">没有找到数据库已有物料</div>`;
          selectedMaterial = null;
          document.getElementById("selectedMaterialText").textContent = "请选择数据库已有物料";
          return;
        }
        host.innerHTML = `
          <label>搜索结果
            <select id="materialPickSelect">
              <option value="">请选择物料</option>
              ${results.map(material => `
                <option value="${material.id}">
                  ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)} · ${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")} · ${material.shelf_name ? `当前：${escapeHtml(material.shelf_name)} / ${material.layer_number} 层 / ${escapeHtml(material.zone_name || "")} 区` : "当前未放入货架"}
                </option>
              `).join("")}
            </select>
          </label>
        `;
        const select = document.getElementById("materialPickSelect");
        select.addEventListener("change", () => {
          selectedMaterial = results.find(item => item.id === Number(select.value)) || null;
          if (!selectedMaterial) {
            document.getElementById("selectedMaterialText").textContent = "请选择数据库已有物料";
            return;
          }
          document.getElementById("selectedMaterialText").textContent = `已选择：${selectedMaterial.material_code} · ${selectedMaterial.name}`;
          if (selectedMaterial.shelf_id) {
            document.getElementById("matShelf").value = selectedMaterial.shelf_id;
            updateLocationSelects(selectedMaterial);
          }
        });
        if (results.length === 1) {
          select.value = String(results[0].id);
          select.dispatchEvent(new Event("change"));
        }
      }

      async function runMaterialPickSearch() {
        const keyword = document.getElementById("materialPickSearch").value.trim();
        const results = keyword ? await api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`) : [];
        const host = document.getElementById("materialPickResults");
        host.innerHTML = results.map(material => `
          <div class="result-row">
            <div>
              <strong>${escapeHtml(material.material_code)} · ${escapeHtml(material.name)}</strong>
              <small>${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")} · ${material.shelf_name ? `当前：${escapeHtml(material.shelf_name)} / ${material.layer_number} 层 / ${escapeHtml(material.zone_name || "")} 区` : "当前未放入货架"}</small>
            </div>
            <button data-pick="${material.id}">选择</button>
          </div>
        `).join("") || `<div class="empty-state">没有找到数据库已有物料</div>`;
        host.querySelectorAll("[data-pick]").forEach(button => {
          button.addEventListener("click", () => {
            selectedMaterial = results.find(item => item.id === Number(button.dataset.pick));
            document.getElementById("selectedMaterialText").textContent = `已选择：${selectedMaterial.material_code} · ${selectedMaterial.name}`;
            if (selectedMaterial.shelf_id) {
              document.getElementById("matShelf").value = selectedMaterial.shelf_id;
              updateLocationSelects(selectedMaterial);
            }
          });
        });
      }
    }

    function updateLocationSelects(preferred = null) {
      const shelf = state.shelves.find(item => item.id === Number(document.getElementById("matShelf").value));
      const layerSelect = document.getElementById("matLayer");
      layerSelect.innerHTML = (shelf?.layers || []).map(layer => `<option value="${layer.layer_number}">${layer.layer_number} 层</option>`).join("");
      if (preferred?.layer_number) layerSelect.value = preferred.layer_number;
      updateZoneSelect(preferred);
    }

    function updateZoneSelect(preferred = null) {
      const shelf = state.shelves.find(item => item.id === Number(document.getElementById("matShelf").value));
      const layer = (shelf?.layers || []).find(item => Number(item.layer_number) === Number(document.getElementById("matLayer").value));
      const zoneSelect = document.getElementById("matZone");
      zoneSelect.innerHTML = (layer?.zones || []).map(zone => `<option value="${zone.name}">${zone.name} 区 · ${escapeHtml(zone.note || "")}</option>`).join("");
      if (preferred?.zone_name) zoneSelect.value = preferred.zone_name;
    }

    function openSearchModal() {
      openModal({
        title: "查找物料",
        body: `
          <div class="form-grid">
            <label class="wide">输入物料编号 / 品牌型号 / 技术规格 / 物料名称
              <input id="searchInput" placeholder="可留空，直接按货架位置筛选">
            </label>
            <label>货架号<select id="searchShelf"><option value="">全部货架</option></select></label>
            <label>货架层<select id="searchLayer"><option value="">全部层</option></select></label>
            <label class="wide">分区<select id="searchZone"><option value="">全部分区</option></select></label>
          </div>
          <div class="dialog-foot" style="padding-left:0;padding-right:0;border-top:0;">
            <button id="runSearch" class="primary">查找</button>
          </div>
          <div class="search-results" id="searchResults"></div>
        `,
        hideOk: true,
        onReady: () => {
          const shelfSelect = document.getElementById("searchShelf");
          shelfSelect.innerHTML = `<option value="">全部货架</option>${state.shelves.map(shelf => `<option value="${shelf.id}">${escapeHtml(shelf.name)}</option>`).join("")}`;
          const updateLocationFilters = () => {
            const shelf = state.shelves.find(item => Number(item.id) === Number(shelfSelect.value));
            const layerSelect = document.getElementById("searchLayer");
            layerSelect.innerHTML = `<option value="">全部层</option>${(shelf?.layers || []).map(layer => `<option value="${layer.layer_number}">${layer.layer_number} 层</option>`).join("")}`;
            const updateZones = () => {
              const layer = (shelf?.layers || []).find(item => Number(item.layer_number) === Number(layerSelect.value));
              document.getElementById("searchZone").innerHTML = `<option value="">全部分区</option>${(layer?.zones || []).map(zone => `<option value="${zone.name}">${escapeHtml(zone.name)} 区 · ${escapeHtml(zone.note || "")}</option>`).join("")}`;
            };
            layerSelect.onchange = updateZones;
            updateZones();
          };
          shelfSelect.addEventListener("change", updateLocationFilters);
          updateLocationFilters();
          const run = async () => {
            const keyword = document.getElementById("searchInput").value.trim();
            const shelfId = Number(shelfSelect.value || 0);
            const layerNumber = Number(document.getElementById("searchLayer").value || 0);
            const zoneName = document.getElementById("searchZone").value;
            const results = await api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`);
            const filtered = results.filter(material =>
              (!shelfId || Number(material.shelf_id) === shelfId) &&
              (!layerNumber || Number(material.layer_number) === layerNumber) &&
              (!zoneName || material.zone_name === zoneName)
            );
            const host = document.getElementById("searchResults");
            host.innerHTML = filtered.map(material => `
              <div class="result-row">
                <div>
                  <strong>${escapeHtml(material.icon || "□")} ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)}</strong>
                  <small>${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")} · ${escapeHtml(material.shelf_name || "未放入货架")} / ${material.layer_number || "-"} 层 / ${escapeHtml(material.zone_name || "")} 区 · 余量 ${formatQty(material.quantity)}</small>
                </div>
                <button data-locate="${material.id}" ${material.shelf_id ? "" : "disabled"}>定位</button>
              </div>
            `).join("") || `<div class="empty-state">没有匹配结果</div>`;
            host.querySelectorAll("[data-locate]").forEach(button => {
              button.addEventListener("click", () => {
                const material = filtered.find(item => item.id === Number(button.dataset.locate));
                closeModal();
                locateMaterial(material);
              });
            });
          };
          document.getElementById("runSearch").addEventListener("click", run);
          document.getElementById("searchInput").addEventListener("keydown", event => { if (event.key === "Enter") run(); });
          document.getElementById("searchInput").focus();
        }
      });
    }

    function locateMaterial(material) {
      state.targetMaterialId = material.id;
      renderHome();
      const shelfTile = document.querySelector(`[data-shelf-id="${material.shelf_id}"]`);
      shelfTile?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
      shelfTile?.classList.add("is-focus");
      markTarget(material.id);
      showSpotlight(material);
    }

    function markTarget(materialId) {
      document.querySelectorAll(".is-target").forEach(node => node.classList.remove("is-target"));
      const material = state.materials.find(item => item.id === Number(materialId));
      if (!material) return;
      const key = `${material.shelf_id}-${material.layer_number}-${material.zone_name}`;
      document.querySelectorAll(`[data-zone-key="${CSS.escape(key)}"]`).forEach(zone => zone.classList.add("is-target"));
    }

    function showSpotlight(material) {
      const shelf = state.shelves.find(item => item.id === material.shelf_id);
      const overlay = document.getElementById("spotlight");
      overlay.innerHTML = `
        <div class="spotlight-inner">
          <div class="viewer-head" style="padding:0 0 12px;border-bottom:0;">
            <div>
              <h2>${escapeHtml(shelf.name)}</h2>
              <span class="hint">${material.layer_number} 层 ${escapeHtml(material.zone_name)} 区 · ${escapeHtml(material.name)}</span>
            </div>
            <div class="viewer-actions">
              <button id="spotOpenShelf" class="primary">进入货架</button>
              <button id="spotClose">关闭</button>
            </div>
          </div>
          <div id="spotShelf"></div>
        </div>
      `;
      const tile = renderShelfTile(shelf, true);
      overlay.querySelector("#spotShelf").appendChild(tile);
      overlay.querySelectorAll(".material-chip").forEach(button => {
        button.addEventListener("click", async () => {
          overlay.classList.remove("open");
          await openViewer(shelf.id, Number(button.dataset.materialId));
        });
      });
      document.getElementById("spotOpenShelf").addEventListener("click", async () => {
        overlay.classList.remove("open");
        await openViewer(shelf.id, material.id);
      });
      document.getElementById("spotClose").addEventListener("click", () => overlay.classList.remove("open"));
      overlay.classList.add("open");
    }

    function openStockModal(material, operation) {
      openModal({
        title: operation === "in" ? "物料入库" : "物料出库",
        body: `
          <div class="facts">
            <div class="fact"><span>物料</span><strong>${escapeHtml(material.material_code)} · ${escapeHtml(material.name)}</strong></div>
            <div class="fact"><span>当前余量</span><strong>${formatQty(material.quantity)} ${escapeHtml(material.unit || "")}</strong></div>
          </div>
          <div class="form-grid">
            <label>数量<input id="stockQty" type="number" min="0.01" step="0.01"></label>
            <label>日期<input id="stockDate" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
            <label class="wide">备注<textarea id="stockRemark"></textarea></label>
          </div>
        `,
        okText: operation === "in" ? "确认入库" : "确认出库",
        onOk: async () => {
          await api(`/api/stock/${operation}`, {
            method: "POST",
            body: JSON.stringify({
              material_id: material.id,
              quantity: Number(document.getElementById("stockQty").value || 0),
              operation_date: document.getElementById("stockDate").value,
              remark: document.getElementById("stockRemark").value.trim()
            })
          });
          await loadBootstrap();
          if (state.currentShelf) await openViewer(state.currentShelf.id, material.id);
          toast(operation === "in" ? "入库已保存" : "出库已保存");
        }
      });
    }

    function openMaterialManageModal() {
      let selectedMaterial = null;
      let searchResults = [];
      openModal({
        title: "物料管理",
        body: `
          <div class="form-grid">
            <label class="wide">搜索物料
              <input id="manageSearchInput" placeholder="输入物料编号 / 物料名称 / 品牌型号 / 技术规格">
            </label>
            <div class="wide">
              <button id="manageRunSearch" class="primary">搜索</button>
              <span class="hint" id="manageSelectedText">请选择要维护的物料</span>
            </div>
            <div class="wide search-results" id="manageResults"></div>
            <div class="wide" id="manageEditor"></div>
          </div>
        `,
        hideOk: true,
        onReady: () => {
          const runSearch = async () => {
            const keyword = document.getElementById("manageSearchInput").value.trim();
            const host = document.getElementById("manageResults");
            document.getElementById("manageEditor").innerHTML = "";
            selectedMaterial = null;
            document.getElementById("manageSelectedText").textContent = "请选择要维护的物料";
            searchResults = keyword ? await api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`) : [];
            if (!searchResults.length) {
              host.innerHTML = `<div class="empty-state">没有找到匹配物料</div>`;
              return;
            }
            host.innerHTML = `
              <label>搜索结果
                <select id="manageResultSelect">
                  <option value="">请选择物料</option>
                  ${searchResults.map(material => `
                    <option value="${material.id}">
                      ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)} · ${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")}
                    </option>
                  `).join("")}
                </select>
              </label>
            `;
            const select = document.getElementById("manageResultSelect");
            select.addEventListener("change", () => {
              selectedMaterial = searchResults.find(item => item.id === Number(select.value)) || null;
              renderManageEditor(selectedMaterial);
            });
            if (searchResults.length === 1) {
              select.value = String(searchResults[0].id);
              select.dispatchEvent(new Event("change"));
            }
          };
          document.getElementById("manageRunSearch").addEventListener("click", runSearch);
          document.getElementById("manageSearchInput").addEventListener("keydown", event => {
            if (event.key === "Enter") runSearch();
          });
          document.getElementById("manageSearchInput").focus();
        }
      });

      function renderManageEditor(material) {
        const editor = document.getElementById("manageEditor");
        if (!material) {
          document.getElementById("manageSelectedText").textContent = "请选择要维护的物料";
          editor.innerHTML = "";
          return;
        }
        document.getElementById("manageSelectedText").textContent = `已选择：${material.material_code} · ${material.name}`;
        editor.innerHTML = `
          <div class="form-grid">
            <label>物料编号<input id="manageCode" value="${escapeAttr(material.material_code || "")}"></label>
            <label>物料名称<input id="manageName" value="${escapeAttr(material.name || "")}"></label>
            <label>品牌型号<input id="manageBrand" value="${escapeAttr(material.brand_model || "")}"></label>
            <label>技术规格<input id="manageSpec" value="${escapeAttr(material.spec || "")}"></label>
            <label>单位<input id="manageUnit" value="${escapeAttr(material.unit || "")}"></label>
            <label>仓库编码<input id="manageWarehouseCode" value="${escapeAttr(material.warehouse_code || "")}"></label>
            <label>大类码<input id="manageMajorCode" value="${escapeAttr(material.major_code || "")}"></label>
            <label>中类码<input id="manageMiddleCode" value="${escapeAttr(material.middle_code || "")}"></label>
            <label>小类码<input id="manageSmallCode" value="${escapeAttr(material.small_code || "")}"></label>
            <label>详细编号<input id="manageDetailCode" value="${escapeAttr(material.detail_code || "")}"></label>
            <label>大类名称<input id="manageCategoryName" value="${escapeAttr(material.category_name || "")}"></label>
            <label>物料类别<input id="manageMaterialType" value="${escapeAttr(material.material_type || "")}"></label>
            <div class="wide dialog-foot" style="padding-left:0;padding-right:0;border-top:0;">
              <button id="manageSave" class="primary">保存修改</button>
              <button id="manageDelete" class="danger">删除物料</button>
            </div>
          </div>
        `;
        document.getElementById("manageSave").addEventListener("click", saveManageMaterial);
        document.getElementById("manageDelete").addEventListener("click", deleteManageMaterial);
      }

      async function saveManageMaterial() {
        if (!selectedMaterial) return;
        const payload = {
          material_code: document.getElementById("manageCode").value.trim(),
          name: document.getElementById("manageName").value.trim(),
          brand_model: document.getElementById("manageBrand").value.trim(),
          spec: document.getElementById("manageSpec").value.trim(),
          unit: document.getElementById("manageUnit").value.trim(),
          warehouse_code: document.getElementById("manageWarehouseCode").value.trim(),
          major_code: document.getElementById("manageMajorCode").value.trim(),
          middle_code: document.getElementById("manageMiddleCode").value.trim(),
          small_code: document.getElementById("manageSmallCode").value.trim(),
          detail_code: document.getElementById("manageDetailCode").value.trim(),
          category_name: document.getElementById("manageCategoryName").value.trim(),
          material_type: document.getElementById("manageMaterialType").value.trim()
        };
        try {
          const response = await api(`/api/material-master/${selectedMaterial.id}`, {
            method: "PUT",
            body: JSON.stringify(payload)
          });
          selectedMaterial = response.material;
          await loadBootstrap();
          if (state.currentShelf) await openViewer(state.currentShelf.id, selectedMaterial.id);
          renderManageEditor(selectedMaterial);
          toast("物料信息已保存");
        } catch (error) {
          toast(error.message || "保存物料失败");
        }
      }

      async function deleteManageMaterial() {
        if (!selectedMaterial) return;
        if (!confirm("确认删除该物料？该物料的料卡记录、库存和货架位置也会一起删除。")) return;
        try {
          const deletedId = selectedMaterial.id;
          await api(`/api/materials/${deletedId}`, { method: "DELETE" });
          await loadBootstrap();
          if (state.currentShelf) await openViewer(state.currentShelf.id);
          searchResults = searchResults.filter(item => item.id !== deletedId);
          selectedMaterial = null;
          document.getElementById("manageSelectedText").textContent = "物料已删除";
          document.getElementById("manageEditor").innerHTML = "";
          document.getElementById("manageResults").innerHTML = searchResults.length
            ? `<label>搜索结果
                <select id="manageResultSelect">
                  <option value="">请选择物料</option>
                  ${searchResults.map(material => `
                    <option value="${material.id}">
                      ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)} · ${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")}
                    </option>
                  `).join("")}
                </select>
              </label>`
            : `<div class="empty-state">没有剩余匹配物料</div>`;
          document.getElementById("manageResultSelect")?.addEventListener("change", event => {
            selectedMaterial = searchResults.find(item => item.id === Number(event.target.value)) || null;
            renderManageEditor(selectedMaterial);
          });
          toast("物料已删除");
        } catch (error) {
          toast(error.message || "删除物料失败");
        }
      }
    }

    function openExportModal() {
      openModal({
        title: "导出料卡 HTML",
        body: `
          <div class="form-grid">
            <label class="wide">导出范围
              <select id="exportMode">
                <option value="all">全部物料</option>
                <option value="one">指定物料编号</option>
              </select>
            </label>
            <label class="wide">物料编号<input id="exportCode" disabled placeholder="例如 RD-R01-0001"></label>
          </div>
        `,
        okText: "打开导出 HTML",
        onReady: () => {
          document.getElementById("exportMode").addEventListener("change", event => {
            document.getElementById("exportCode").disabled = event.target.value !== "one";
          });
        },
        onOk: () => {
          const mode = document.getElementById("exportMode").value;
          const code = document.getElementById("exportCode").value.trim();
          window.open(mode === "one" ? `/api/export?material_code=${encodeURIComponent(code)}` : "/api/export", "_blank");
        }
      });
    }

    function openModal({ title, body, okText = "确定", hideOk = false, onOk, onReady }) {
      const modal = document.getElementById("modal");
      modal.innerHTML = `
        <div class="dialog">
          <div class="dialog-head">
            <h3>${escapeHtml(title)}</h3>
            <button id="modalClose">×</button>
          </div>
          <div class="dialog-body">${body}</div>
          <div class="dialog-foot">
            <button id="modalCancel">取消</button>
            ${hideOk ? "" : `<button id="modalOk" class="primary">${escapeHtml(okText)}</button>`}
          </div>
        </div>
      `;
      modal.classList.add("open");
      document.getElementById("modalClose").addEventListener("click", closeModal);
      document.getElementById("modalCancel").addEventListener("click", closeModal);
      if (!hideOk) {
        document.getElementById("modalOk").addEventListener("click", async () => {
          try {
            await onOk?.();
            closeModal();
          } catch (error) {
            toast(error.message || "操作失败");
          }
        });
      }
      onReady?.();
    }

    function closeModal() {
      document.getElementById("modal").classList.remove("open");
    }

    function toast(message, duration = 2600) {
      const node = document.getElementById("toast");
      node.textContent = message;
      node.classList.add("show");
      clearTimeout(node.timer);
      node.timer = setTimeout(() => node.classList.remove("show"), duration);
    }

    function clampInt(value, min, max) {
      return Math.max(min, Math.min(max, parseInt(value, 10) || min));
    }

    function formatQty(value) {
      const number = Number(value || 0);
      return Number.isInteger(number) ? String(number) : number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    }

    function recordDisplayType(record) {
      const displayType = record.display_type || "";
      if (displayType) return displayType;
      const remark = String(record.remark || "").toLowerCase();
      if (remark.includes("归还") || remark.includes("return")) return "return";
      if (remark.includes("借用") || remark.includes("borrow")) return "borrow";
      return record.operation_type || "";
    }

    function stockRecordTypeLabel(type) {
      return ({ in: "入库", out: "出库", borrow: "借用", return: "归还" })[type] || type || "";
    }

    function stockRecordsTable(records, types, title, emptyText) {
      const typeSet = new Set(types);
      const rows = (records || []).filter(record => typeSet.has(recordDisplayType(record))).slice().reverse();
      return `<h4>${escapeHtml(title)}</h4><table class="records">
        <thead><tr><th>日期</th><th>类型</th><th>数量</th><th>余量</th></tr></thead>
        <tbody>${rows.map(record => `
          <tr>
            <td>${escapeHtml(record.operation_date)}</td>
            <td>${escapeHtml(stockRecordTypeLabel(recordDisplayType(record)))}</td>
            <td>${formatQty(record.quantity)}</td>
            <td>${formatQty(record.balance_after)}</td>
          </tr>
        `).join("") || `<tr><td colspan="4">${escapeHtml(emptyText)}</td></tr>`}</tbody>
      </table>`;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    function escapeAttr(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }
