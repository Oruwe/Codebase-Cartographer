/* Orgono 3D cartographer.
 * A force-directed knowledge-graph viewer built directly on three.js:
 *  - Barnes-Hut octree N-body repulsion (O(n log n)), so layouts stay interactive
 *    well past the point where a naive O(n^2) loop stalls.
 *  - InstancedMesh nodes + a single LineSegments buffer for edges.
 *  - Impact focus: click a node to trace its call/import neighbourhood to a
 *    chosen depth; everything outside the subgraph dims rather than disappears,
 *    so you keep your bearings.
 * No network access: three.js is vendored and the graph is served locally.
 */
(function () {
  "use strict";

  var KIND_COLORS = {
    file: 0xffb454, function: 0x5ad1ff, method: 0x49e6a0, class: 0xc792ea,
    interface: 0xf78c6c, constant: 0xffd479, external: 0x5b6676, unparsed: 0xff5370
  };
  var EDGE_COLORS = {
    calls: 0x5ad1ff, imports: 0xffb454, defines: 0x8b7cff, references: 0x3f4a5a
  };

  var state = {
    nodes: [], edges: [], byId: {}, adj: {},
    focus: null, depth: 2, inFocus: null,
    activeEdgeTypes: {}, activeKinds: {},
    running: true, isolate: false,
    repulsion: 220, linkDist: 42, alpha: 1, ticks: 0, framed: false, extent: 300
  };

  var MIN_DIST2 = 36;      // no pair may act as if closer than 6 units
  var MAX_SPEED = 30;      // per-tick displacement ceiling
  var MAX_COORD = 20000;   // hard bound on position, a last line of defence

  var scene, camera, renderer, raycaster, nodeMesh, edgeLines, hoverIdx = -1;
  var dummy, tmpColor, labelGroup;

  /* ---------- Barnes-Hut octree ---------- */
  function Octree(cx, cy, cz, half) {
    this.cx = cx; this.cy = cy; this.cz = cz; this.half = half;
    this.mass = 0; this.mx = 0; this.my = 0; this.mz = 0;
    this.children = null; this.body = null;
  }
  Octree.prototype.insert = function (n) {
    if (this.body === null && this.children === null) { this.body = n; this.accum(n); return; }
    if (this.children === null) {
      var existing = this.body; this.body = null; this.subdivide();
      this.place(existing);
    }
    this.accum(n); this.place(n);
  };
  Octree.prototype.accum = function (n) {
    var m = this.mass + 1;
    this.mx = (this.mx * this.mass + n.x) / m;
    this.my = (this.my * this.mass + n.y) / m;
    this.mz = (this.mz * this.mass + n.z) / m;
    this.mass = m;
  };
  Octree.prototype.subdivide = function () {
    var h = this.half / 2; this.children = [];
    for (var i = 0; i < 8; i++) {
      this.children.push(new Octree(
        this.cx + ((i & 1) ? h : -h),
        this.cy + ((i & 2) ? h : -h),
        this.cz + ((i & 4) ? h : -h), h));
    }
  };
  Octree.prototype.place = function (n) {
    if (this.children === null) this.subdivide();
    var i = (n.x > this.cx ? 1 : 0) | (n.y > this.cy ? 2 : 0) | (n.z > this.cz ? 4 : 0);
    if (this.children[i].half < 0.5) { this.children[i].accum(n); return; }
    this.children[i].insert(n);
  };
  Octree.prototype.force = function (n, theta, k, out) {
    if (this.mass === 0) return;
    var dx = this.mx - n.x, dy = this.my - n.y, dz = this.mz - n.z;
    var d2 = dx * dx + dy * dy + dz * dz;
    if (d2 < 1e-6) { // jitter coincident bodies apart deterministically
      out.x += (n.seed % 7 - 3) * 0.4; out.y += (n.seed % 5 - 2) * 0.4; out.z += (n.seed % 3 - 1) * 0.4;
      return;
    }
    // Minimum-distance clamp. Without it the 1/d^2 term is a singularity: two
    // nearly-coincident nodes generate unbounded velocity and the whole
    // simulation diverges (measured: bounding radius reached 2.7e11).
    if (d2 < MIN_DIST2) d2 = MIN_DIST2;
    if (this.children === null || (this.half * 2) / Math.sqrt(d2) < theta) {
      // d3-force manyBody: v += delta * (strength / d^2). Net magnitude is
      // strength/d, and the sign is negated so the force pushes apart.
      var w = -(k * this.mass) / d2;
      out.x += dx * w; out.y += dy * w; out.z += dz * w;
      return;
    }
    for (var i = 0; i < 8; i++) this.children[i].force(n, theta, k, out);
  };

  /* ---------- layout ----------
   * Force balance follows the d3-force model rather than raw Fruchterman-Reingold:
   * repulsion contributes |v| += strength/d (applied as delta * strength / d^2) and
   * links pull with (d - rest)/d. An earlier version used k^2/d^2 with k=140, i.e.
   * a repulsion ~650x too strong, and the graph exploded into a sparse dust cloud.
   * Found by rendering it headlessly and measuring the bounding radius, not by
   * reading the formula.
   */
  function step() {
    var N = state.nodes.length; if (!N) return;
    var i, n;
    var ext = 1;
    for (i = 0; i < N; i++) {
      n = state.nodes[i];
      ext = Math.max(ext, Math.abs(n.x), Math.abs(n.y), Math.abs(n.z));
    }
    state.extent = ext;
    var tree = new Octree(0, 0, 0, ext + 10);
    for (i = 0; i < N; i++) tree.insert(state.nodes[i]);

    var strength = state.repulsion;           // positive magnitude; applied as repulsion
    var out = { x: 0, y: 0, z: 0 };
    for (i = 0; i < N; i++) {
      n = state.nodes[i];
      out.x = 0; out.y = 0; out.z = 0;
      tree.force(n, 0.9, strength, out);
      n.vx += out.x * state.alpha;
      n.vy += out.y * state.alpha;
      n.vz += out.z * state.alpha;
    }

    // links: pull toward the rest length, weighted down for hub nodes so a
    // high-degree node is not dragged apart by every neighbour at once.
    for (i = 0; i < state.edges.length; i++) {
      var e = state.edges[i];
      var a = state.byId[e.src], b = state.byId[e.dst];
      if (!a || !b) continue;
      var dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
      var d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
      var w = ((d - state.linkDist) / d) * state.alpha * 0.5;
      var bias = 1 / Math.min(a.degree || 1, b.degree || 1);
      var wx = dx * w * bias, wy = dy * w * bias, wz = dz * w * bias;
      a.vx += wx; a.vy += wy; a.vz += wz;
      b.vx -= wx; b.vy -= wy; b.vz -= wz;
    }

    // weak centring so disconnected components stay in frame, then integrate
    for (i = 0; i < N; i++) {
      n = state.nodes[i];
      n.vx -= n.x * 0.012 * state.alpha;
      n.vy -= n.y * 0.012 * state.alpha;
      n.vz -= n.z * 0.012 * state.alpha;
      n.vx *= 0.6; n.vy *= 0.6; n.vz *= 0.6;   // velocity decay
      var sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy + n.vz * n.vz);
      if (sp > MAX_SPEED) { var k2 = MAX_SPEED / sp; n.vx *= k2; n.vy *= k2; n.vz *= k2; }
      n.x += n.vx; n.y += n.vy; n.z += n.vz;
      if (!isFinite(n.x) || Math.abs(n.x) > MAX_COORD) { n.x = Math.max(-MAX_COORD, Math.min(MAX_COORD, n.x || 0)); n.vx = 0; }
      if (!isFinite(n.y) || Math.abs(n.y) > MAX_COORD) { n.y = Math.max(-MAX_COORD, Math.min(MAX_COORD, n.y || 0)); n.vy = 0; }
      if (!isFinite(n.z) || Math.abs(n.z) > MAX_COORD) { n.z = Math.max(-MAX_COORD, Math.min(MAX_COORD, n.z || 0)); n.vz = 0; }
    }
    state.alpha += (0.001 - state.alpha) * 0.0228;   // alpha decay toward rest
    state.ticks++;
    if (state.ticks >= 60 && !state.framed) { frameAll(); state.framed = true; }
    if (state.framed && state.ticks % 120 === 0) frameAll();
  }

  /* Fit the camera to the graph's bounding sphere. */
  function frameAll() {
    var N = state.nodes.length; if (!N) return;
    var cx = 0, cy = 0, cz = 0, i, n;
    for (i = 0; i < N; i++) { n = state.nodes[i]; cx += n.x; cy += n.y; cz += n.z; }
    cx /= N; cy /= N; cz /= N;
    var r = 1;
    for (i = 0; i < N; i++) {
      n = state.nodes[i];
      r = Math.max(r, Math.hypot(n.x - cx, n.y - cy, n.z - cz));
    }
    cam.tTarget.set(cx, cy, cz);
    var fov = camera.fov * Math.PI / 180;
    cam.tRadius = Math.min(20000, (r * 1.15) / Math.tan(fov / 2) + 60);
    // The HUD covers the left of the viewport on wide screens, so shift the
    // orbit target to centre the graph in the space that is actually visible.
    if (window.innerWidth > 760) {
      var hud = document.getElementById("hud");
      var panel = (hud ? hud.getBoundingClientRect().right + 14 : 0);
      var frac = (panel / window.innerWidth) / 2;
      var worldWidth = 2 * Math.tan(fov / 2) * camera.aspect * cam.tRadius;
      var right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0).normalize();
      cam.tTarget.addScaledVector(right, -worldWidth * frac);
    }
    // FogExp2 is 1 - exp(-(d*density)^2): a fixed density blanks out any graph
    // bigger than a few hundred units, so tie it to the bounding radius.
    if (scene.fog) scene.fog.density = 0.55 / Math.max(r, 1);
    camera.far = Math.max(12000, cam.tRadius * 4);
    camera.updateProjectionMatrix();
  }

  /* ---------- scene ---------- */
  function initScene() {
    var stage = document.getElementById("stage");
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0c10);
    scene.fog = new THREE.FogExp2(0x0a0c10, 0.0016);

    camera = new THREE.PerspectiveCamera(58, window.innerWidth / window.innerHeight, 0.5, 12000);
    camera.position.set(0, 0, 620);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    stage.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.62));
    var key = new THREE.DirectionalLight(0xffffff, 0.85); key.position.set(1, 1, 1); scene.add(key);
    var rim = new THREE.DirectionalLight(0x5ad1ff, 0.34); rim.position.set(-1, -0.5, -1); scene.add(rim);

    raycaster = new THREE.Raycaster();
    dummy = new THREE.Object3D();
    tmpColor = new THREE.Color();
    labelGroup = new THREE.Group(); scene.add(labelGroup);

    window.addEventListener("resize", onResize);
  }

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  function buildMeshes() {
    if (nodeMesh) { scene.remove(nodeMesh); nodeMesh.geometry.dispose(); nodeMesh.material.dispose(); }
    if (edgeLines) { scene.remove(edgeLines); edgeLines.geometry.dispose(); edgeLines.material.dispose(); }

    var geo = new THREE.SphereGeometry(1, 14, 10);
    var mat = new THREE.MeshLambertMaterial({ transparent: true });
    nodeMesh = new THREE.InstancedMesh(geo, mat, state.nodes.length);
    nodeMesh.instanceColor = new THREE.InstancedBufferAttribute(
      new Float32Array(state.nodes.length * 3), 3);
    nodeMesh.count = state.nodes.length;
    scene.add(nodeMesh);

    var positions = new Float32Array(state.edges.length * 6);
    var colors = new Float32Array(state.edges.length * 6);
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    edgeLines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.4, depthWrite: false
    }));
    scene.add(edgeLines);
  }

  /* Node size scales with the graph's extent. Without this a large graph frames
   * out to a camera distance where every node is sub-pixel and the view looks
   * empty -- which is exactly what the first headless render showed. */
  function nodeRadius(n) {
    var base = n.kind === "file" ? 4.2 : n.kind === "class" ? 3.6 : n.kind === "external" ? 2.0 : 2.8;
    var scale = Math.max(1, state.extent / 650);
    return (base + Math.min(Math.sqrt(n.degree || 0) * 0.85, 7)) * scale;
  }

  function visible(n) {
    if (state.activeKinds[n.kind] === false) return false;
    if (state.isolate && state.inFocus && !state.inFocus[n.id]) return false;
    return true;
  }

  function updateInstances() {
    if (!nodeMesh) return;
    var i, n, dim, r;
    for (i = 0; i < state.nodes.length; i++) {
      n = state.nodes[i];
      var shown = visible(n);
      r = shown ? nodeRadius(n) : 0.0001;
      if (state.inFocus && !state.inFocus[n.id] && !state.isolate) r *= 0.55;
      dummy.position.set(n.x, n.y, n.z);
      dummy.scale.setScalar(r);
      dummy.updateMatrix();
      nodeMesh.setMatrixAt(i, dummy.matrix);

      tmpColor.setHex(KIND_COLORS[n.kind] || 0x888888);
      if (state.inFocus && !state.inFocus[n.id]) tmpColor.multiplyScalar(0.28);
      if (i === hoverIdx) tmpColor.setHex(0xffffff);
      if (state.focus === n.id) tmpColor.setHex(0xffffff);
      nodeMesh.instanceColor.setXYZ(i, tmpColor.r, tmpColor.g, tmpColor.b);
    }
    nodeMesh.instanceMatrix.needsUpdate = true;
    nodeMesh.instanceColor.needsUpdate = true;

    var pos = edgeLines.geometry.attributes.position.array;
    var col = edgeLines.geometry.attributes.color.array;
    for (i = 0; i < state.edges.length; i++) {
      var e = state.edges[i];
      var a = state.byId[e.src], b = state.byId[e.dst];
      var o = i * 6;
      if (!a || !b || state.activeEdgeTypes[e.type] === false || !visible(a) || !visible(b)) {
        pos[o] = pos[o+1] = pos[o+2] = pos[o+3] = pos[o+4] = pos[o+5] = 0;
        continue;
      }
      pos[o] = a.x; pos[o+1] = a.y; pos[o+2] = a.z;
      pos[o+3] = b.x; pos[o+4] = b.y; pos[o+5] = b.z;
      tmpColor.setHex(EDGE_COLORS[e.type] || 0x555555);
      var onPath = state.inFocus && state.inFocus[e.src] && state.inFocus[e.dst];
      if (state.inFocus && !onPath) tmpColor.multiplyScalar(0.16);
      else if (onPath) tmpColor.multiplyScalar(1.6);
      col[o] = col[o+3] = tmpColor.r;
      col[o+1] = col[o+4] = tmpColor.g;
      col[o+2] = col[o+5] = tmpColor.b;
    }
    edgeLines.geometry.attributes.position.needsUpdate = true;
    edgeLines.geometry.attributes.color.needsUpdate = true;
  }


  /* ---------- labels ----------
   * Sprites are pooled and only ever attached to the highest-degree nodes plus
   * whatever is hovered or focused: drawing 661 text textures would cost more
   * than the graph itself, and an unreadable thicket of names is worse than none.
   */
  var LABEL_POOL = 48;
  var labelSprites = [];

  function makeLabelTexture(text) {
    var c = document.createElement("canvas");
    var ctx = c.getContext("2d");
    var font = "600 34px ui-sans-serif, -apple-system, 'Segoe UI', Roboto, sans-serif";
    ctx.font = font;
    var w = Math.min(Math.ceil(ctx.measureText(text).width) + 28, 620);
    c.width = w; c.height = 56;
    ctx = c.getContext("2d");
    ctx.font = font;
    ctx.textBaseline = "middle";
    ctx.fillStyle = "rgba(8,10,14,0.80)";
    roundRect(ctx, 0, 6, w, 44, 10); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.14)"; ctx.lineWidth = 2;
    roundRect(ctx, 1, 7, w - 2, 42, 9); ctx.stroke();
    ctx.fillStyle = "#e7ecf3";
    ctx.fillText(text, 14, 29);
    var tex = new THREE.CanvasTexture(c);
    tex.minFilter = THREE.LinearFilter;
    return { tex: tex, w: w, h: 56 };
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function initLabels() {
    for (var i = 0; i < LABEL_POOL; i++) {
      var mat = new THREE.SpriteMaterial({ transparent: true, depthTest: false, opacity: 0 });
      var sp = new THREE.Sprite(mat);
      sp.visible = false;
      sp.renderOrder = 10;
      labelGroup.add(sp);
      labelSprites.push({ sprite: sp, nodeId: null, tex: null });
    }
  }

  function labelTargets() {
    var pick = [];
    if (state.inFocus) {
      for (var i = 0; i < state.nodes.length; i++) {
        var n = state.nodes[i];
        if (state.inFocus[n.id] && n.kind !== "external" && visible(n)) pick.push(n);
      }
      pick.sort(function (a, b) { return (b.degree || 0) - (a.degree || 0); });
    } else {
      pick = state.nodes.filter(function (n) {
        return n.kind !== "external" && visible(n);
      }).sort(function (a, b) { return (b.degree || 0) - (a.degree || 0); });
    }
    if (hoverIdx >= 0 && pick.indexOf(state.nodes[hoverIdx]) === -1) {
      pick.unshift(state.nodes[hoverIdx]);
    }
    return pick.slice(0, 160);   // candidates; screen-space culling picks the survivors
  }

  /* Labels are culled in screen space: a candidate that would overlap an
   * already-placed label is skipped. Without this the dense core of a real
   * repository renders as an unreadable pile of overlapping names. Highest
   * degree wins, so the important symbols are the ones that survive.
   */
  var _proj = null;
  function updateLabels() {
    if (!labelSprites.length) return;
    if (!_proj) _proj = new THREE.Vector3();
    var targets = labelTargets();
    var placed = [];
    var used = 0;
    var vFov = camera.fov * Math.PI / 180;

    for (var t = 0; t < targets.length && used < labelSprites.length; t++) {
      var n = targets[t];
      _proj.set(n.x, n.y, n.z).project(camera);
      if (_proj.z > 1) continue;                       // behind the camera
      var sx = (_proj.x * 0.5 + 0.5) * window.innerWidth;
      var sy = (-_proj.y * 0.5 + 0.5) * window.innerHeight;
      if (sx < -200 || sx > window.innerWidth + 200) continue;

      var approxW = Math.min(n.name.length * 7.4 + 20, 300);
      var box = { x: sx, y: sy, w: approxW, h: 21 };
      var clash = false;
      for (var q = 0; q < placed.length; q++) {
        var o = placed[q];
        if (Math.abs(box.x - o.x) * 2 < (box.w + o.w) &&
            Math.abs(box.y - o.y) * 2 < (box.h + o.h)) { clash = true; break; }
      }
      if (clash && state.focus !== n.id) continue;
      placed.push(box);

      var slot = labelSprites[used++];
      if (slot.nodeId !== n.id) {
        var made = makeLabelTexture(n.name);
        if (slot.tex) slot.tex.dispose();
        slot.tex = made.tex;
        slot.sprite.material.map = made.tex;
        slot.sprite.material.needsUpdate = true;
        slot.sprite.userData.aspect = made.w / made.h;
        slot.nodeId = n.id;
      }
      var dist = camera.position.distanceTo(_proj.set(n.x, n.y, n.z));
      var h = 2 * Math.tan(vFov / 2) * dist * (17 / window.innerHeight);
      slot.sprite.scale.set(h * (slot.sprite.userData.aspect || 4), h, 1);
      slot.sprite.position.set(n.x, n.y + nodeRadius(n) + h * 0.75, n.z);
      slot.sprite.material.opacity = (state.focus === n.id) ? 1 : 0.85;
      slot.sprite.visible = true;
    }
    for (var i = used; i < labelSprites.length; i++) labelSprites[i].sprite.visible = false;
  }

  /* ---------- camera controls (self-contained, no OrbitControls) ---------- */
  var cam = { theta: 0.6, phi: 1.15, radius: 620, target: new THREE.Vector3(), 
              tTheta: 0.6, tPhi: 1.15, tRadius: 620, tTarget: new THREE.Vector3() };
  function applyCamera() {
    cam.theta += (cam.tTheta - cam.theta) * 0.14;
    cam.phi += (cam.tPhi - cam.phi) * 0.14;
    cam.radius += (cam.tRadius - cam.radius) * 0.12;
    cam.target.lerp(cam.tTarget, 0.12);
    var sp = Math.sin(cam.phi);
    camera.position.set(
      cam.target.x + cam.radius * sp * Math.sin(cam.theta),
      cam.target.y + cam.radius * Math.cos(cam.phi),
      cam.target.z + cam.radius * sp * Math.cos(cam.theta));
    camera.lookAt(cam.target);
  }
  function initControls(el) {
    var dragging = false, panning = false, lx = 0, ly = 0;
    el.addEventListener("pointerdown", function (ev) {
      dragging = true; panning = ev.button === 2 || ev.shiftKey;
      lx = ev.clientX; ly = ev.clientY; el.setPointerCapture(ev.pointerId);
    });
    el.addEventListener("pointerup", function (ev) {
      dragging = false; try { el.releasePointerCapture(ev.pointerId); } catch (e) {}
    });
    el.addEventListener("pointermove", function (ev) {
      onHover(ev);
      if (!dragging) return;
      var dx = ev.clientX - lx, dy = ev.clientY - ly; lx = ev.clientX; ly = ev.clientY;
      if (panning) {
        var right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
        var up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
        var s = cam.radius * 0.0016;
        cam.tTarget.addScaledVector(right, -dx * s).addScaledVector(up, dy * s);
      } else {
        cam.tTheta -= dx * 0.005;
        cam.tPhi = Math.max(0.08, Math.min(Math.PI - 0.08, cam.tPhi - dy * 0.005));
      }
    });
    el.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      cam.tRadius = Math.max(30, Math.min(6000, cam.tRadius * (1 + Math.sign(ev.deltaY) * 0.12)));
    }, { passive: false });
    el.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });
    el.addEventListener("click", function (ev) {
      if (hoverIdx >= 0) setFocus(state.nodes[hoverIdx].id);
    });
  }

  /* ---------- picking ---------- */
  var pointer = new THREE.Vector2(-10, -10), lastMouse = { x: 0, y: 0 };
  function onHover(ev) {
    lastMouse.x = ev.clientX; lastMouse.y = ev.clientY;
    pointer.x = (ev.clientX / window.innerWidth) * 2 - 1;
    pointer.y = -(ev.clientY / window.innerHeight) * 2 + 1;
  }
  function pick() {
    if (!nodeMesh) return;
    raycaster.setFromCamera(pointer, camera);
    var hits = raycaster.intersectObject(nodeMesh);
    var idx = -1;
    for (var i = 0; i < hits.length; i++) {
      var id = hits[i].instanceId;
      if (id != null && visible(state.nodes[id])) { idx = id; break; }
    }
    if (idx !== hoverIdx) {
      hoverIdx = idx;
      var tip = document.getElementById("tooltip");
      if (idx >= 0) {
        var n = state.nodes[idx];
        tip.innerHTML = '<div class="t-name"></div><div class="t-meta"></div>';
        tip.querySelector(".t-name").textContent = n.name;
        tip.querySelector(".t-meta").textContent =
          n.kind + (n.path ? " · " + n.path + (n.start_line ? ":" + n.start_line : "") : "") +
          " · " + (n.degree || 0) + " links";
        tip.hidden = false;
        document.body.style.cursor = "pointer";
      } else { tip.hidden = true; document.body.style.cursor = ""; }
    }
    if (hoverIdx >= 0) {
      var t = document.getElementById("tooltip");
      t.style.left = Math.min(lastMouse.x + 14, window.innerWidth - 340) + "px";
      t.style.top = (lastMouse.y + 16) + "px";
    }
  }

  /* ---------- focus / impact ---------- */
  function computeFocus() {
    if (!state.focus) { state.inFocus = null; return; }
    var seen = {}; seen[state.focus] = true;
    var frontier = [state.focus];
    for (var d = 0; d < state.depth; d++) {
      var next = [];
      for (var i = 0; i < frontier.length; i++) {
        var nb = state.adj[frontier[i]] || [];
        for (var j = 0; j < nb.length; j++) {
          var e = nb[j];
          if (state.activeEdgeTypes[e.type] === false) continue;
          if (!seen[e.other]) { seen[e.other] = true; next.push(e.other); }
        }
      }
      frontier = next;
      if (!frontier.length) break;
    }
    state.inFocus = seen;
  }
  function setFocus(id) {
    state.focus = id;
    computeFocus();
    var n = state.byId[id];
    var info = document.getElementById("focus-info");
    if (n) {
      var count = Object.keys(state.inFocus || {}).length - 1;
      info.innerHTML = "";
      var strong = document.createElement("div");
      strong.textContent = n.name;
      strong.style.color = "#" + (KIND_COLORS[n.kind] || 0xffffff).toString(16).padStart(6, "0");
      strong.style.fontWeight = "600";
      var meta = document.createElement("div");
      meta.className = "small muted";
      meta.textContent = n.kind + (n.path ? " · " + n.path + ":" + n.start_line : "") +
        " — " + count + " nodes within " + state.depth + " hop" + (state.depth > 1 ? "s" : "");
      info.appendChild(strong); info.appendChild(meta);
      // Frame the focused subgraph by its real spatial extent. Sizing the
      // camera from the node *count* put it inside the cloud on any large
      // focus, because node radii scale with the graph's extent.
      var cx = 0, cy = 0, cz = 0, m = 0, i, q;
      for (i = 0; i < state.nodes.length; i++) {
        q = state.nodes[i];
        if (state.inFocus[q.id]) { cx += q.x; cy += q.y; cz += q.z; m++; }
      }
      if (m) {
        cx /= m; cy /= m; cz /= m;
        var rad = 1;
        for (i = 0; i < state.nodes.length; i++) {
          q = state.nodes[i];
          if (state.inFocus[q.id]) {
            rad = Math.max(rad, Math.hypot(q.x - cx, q.y - cy, q.z - cz) + nodeRadius(q));
          }
        }
        var fov2 = camera.fov * Math.PI / 180;
        cam.tTarget.set(cx, cy, cz);
        cam.tRadius = Math.min(20000, (rad * 1.3) / Math.tan(fov2 / 2) + 40);
      } else {
        cam.tTarget.set(n.x, n.y, n.z);
        cam.tRadius = Math.max(150, nodeRadius(n) * 14);
      }
    }
  }
  function clearFocus() {
    state.focus = null; state.inFocus = null; state.isolate = false;
    document.getElementById("isolate").setAttribute("aria-pressed", "false");
    document.getElementById("focus-info").textContent = "Click a node to trace its impact.";
  }

  /* ---------- UI ---------- */
  function chip(label, color, on, cb) {
    var el = document.createElement("span");
    el.className = "chip"; el.setAttribute("aria-pressed", String(on));
    el.setAttribute("role", "button"); el.tabIndex = 0;
    var dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = "#" + color.toString(16).padStart(6, "0");
    el.appendChild(dot);
    el.appendChild(document.createTextNode(label));
    function toggle() {
      var next = el.getAttribute("aria-pressed") !== "true";
      el.setAttribute("aria-pressed", String(next)); cb(next);
    }
    el.addEventListener("click", toggle);
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    return el;
  }

  function buildUI(meta) {
    document.getElementById("repo-name").textContent = meta.root || "";
    var et = document.getElementById("edge-toggles");
    Object.keys(EDGE_COLORS).forEach(function (t) {
      if (!state.edges.some(function (e) { return e.type === t; })) return;
      state.activeEdgeTypes[t] = true;
      et.appendChild(chip(t, EDGE_COLORS[t], true, function (on) {
        state.activeEdgeTypes[t] = on; computeFocus();
      }));
    });
    var kt = document.getElementById("kind-toggles");
    Object.keys(KIND_COLORS).forEach(function (k) {
      if (!state.nodes.some(function (n) { return n.kind === k; })) return;
      state.activeKinds[k] = true;
      kt.appendChild(chip(k, KIND_COLORS[k], true, function (on) { state.activeKinds[k] = on; }));
    });
    var lg = document.getElementById("legend");
    lg.innerHTML = "<div style='color:#e7ecf3;font-weight:600;margin-bottom:5px'>edges</div>";
    Object.keys(EDGE_COLORS).forEach(function (t) {
      var row = document.createElement("div");
      var d = document.createElement("span");
      d.className = "dot"; d.style.background = "#" + EDGE_COLORS[t].toString(16).padStart(6, "0");
      row.appendChild(d); row.appendChild(document.createTextNode(t));
      lg.appendChild(row);
    });

    var search = document.getElementById("search");
    var results = document.getElementById("results");
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      results.innerHTML = "";
      if (!q) { results.hidden = true; return; }
      var hits = state.nodes.filter(function (n) {
        return n.name.toLowerCase().indexOf(q) >= 0;
      }).sort(function (a, b) { return (b.degree || 0) - (a.degree || 0); }).slice(0, 40);
      hits.forEach(function (n) {
        var row = document.createElement("div");
        row.textContent = n.name + " ";
        var k = document.createElement("span");
        k.className = "k"; k.textContent = n.kind + (n.path ? " · " + n.path : "");
        row.appendChild(k);
        row.addEventListener("click", function () { setFocus(n.id); results.hidden = true; search.value = ""; });
        results.appendChild(row);
      });
      results.hidden = hits.length === 0;
    });

    var depth = document.getElementById("depth");
    depth.addEventListener("input", function () {
      state.depth = parseInt(depth.value, 10);
      document.getElementById("depth-out").textContent = depth.value;
      if (state.focus) setFocus(state.focus);
    });
    document.getElementById("clear-focus").addEventListener("click", clearFocus);
    var iso = document.getElementById("isolate");
    iso.addEventListener("click", function () {
      state.isolate = !state.isolate;
      iso.setAttribute("aria-pressed", String(state.isolate));
    });
    document.getElementById("reheat").addEventListener("click", function () {
      state.alpha = 1; state.ticks = 0; state.framed = false;
    });
    var pause = document.getElementById("pause");
    pause.addEventListener("click", function () {
      state.running = !state.running;
      pause.textContent = state.running ? "Pause" : "Resume";
      pause.setAttribute("aria-pressed", String(!state.running));
    });
    document.getElementById("reset-cam").addEventListener("click", function () {
      cam.tTheta = 0.6; cam.tPhi = 1.15; frameAll();
    });
    document.getElementById("repulsion").addEventListener("input", function (e) {
      state.repulsion = parseInt(e.target.value, 10); state.alpha = Math.max(state.alpha, 0.5);
    });
    document.getElementById("linkdist").addEventListener("input", function (e) {
      state.linkDist = parseInt(e.target.value, 10); state.alpha = Math.max(state.alpha, 0.5);
    });

    document.addEventListener("keydown", function (e) {
      if (e.target.tagName === "INPUT") {
        if (e.key === "Escape") { e.target.blur(); results.hidden = true; }
        return;
      }
      if (e.key === "/") { e.preventDefault(); search.focus(); }
      if (e.key === "Escape") clearFocus();
      if (e.key === "f") { if (state.focus) setFocus(state.focus); else frameAll(); }
      if (e.key === " ") { e.preventDefault(); document.getElementById("pause").click(); }
    });
  }

  /* ---------- boot ---------- */
  function hydrate(data) {
    var seed = 1;
    function rnd() { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; }
    state.nodes = data.nodes.map(function (n, i) {
      var r = 180 + rnd() * 120, a = rnd() * Math.PI * 2, b = Math.acos(2 * rnd() - 1);
      return Object.assign({}, n, {
        x: r * Math.sin(b) * Math.cos(a), y: r * Math.sin(b) * Math.sin(a), z: r * Math.cos(b),
        vx: 0, vy: 0, vz: 0, degree: 0, seed: i + 1
      });
    });
    state.nodes.forEach(function (n) { state.byId[n.id] = n; });
    state.edges = data.edges.filter(function (e) { return state.byId[e.src] && state.byId[e.dst]; });
    state.edges.forEach(function (e) {
      state.byId[e.src].degree++; state.byId[e.dst].degree++;
      (state.adj[e.src] = state.adj[e.src] || []).push({ other: e.dst, type: e.type });
      (state.adj[e.dst] = state.adj[e.dst] || []).push({ other: e.src, type: e.type });
    });
    document.getElementById("stats").textContent =
      state.nodes.length + " nodes · " + state.edges.length + " edges" +
      (data.stats && data.stats.languages ? " · " + Object.keys(data.stats.languages).join(", ") : "");
  }

  function animate() {
    requestAnimationFrame(animate);
    if (state.running) {
      // Physics is decoupled from the render rate: while the layout is still hot
      // we run as many ticks as fit in a small time budget, so the graph settles
      // in about the same wall-clock time on a software renderer as on a GPU.
      var budget = performance.now() + 9;
      var guard = 0;
      do { step(); guard++; }
      while (state.alpha > 0.02 && performance.now() < budget && guard < 40);
    }
    applyCamera();
    pick();
    updateInstances();
    updateLabels();
    renderer.render(scene, camera);
  }

  function boot(data) {
    hydrate(data);
    window.__orgono_state = state;   // read-only handle for diagnostics/tests
    initScene();
    buildMeshes();
    initLabels();
    buildUI(data);
    initControls(renderer.domElement);
    document.getElementById("loading").hidden = true;
    animate();
  }

  fetch("graph.json")
    .then(function (r) {
      if (!r.ok) throw new Error("graph.json " + r.status);
      return r.json();
    })
    .then(boot)
    .catch(function (err) {
      var el = document.getElementById("loading");
      el.textContent = "could not load graph.json — " + err.message;
      el.hidden = false;
    });
})();
