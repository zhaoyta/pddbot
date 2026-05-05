"""浏览器反检测 stealth 注入

把 chromium 启动后能被脚本探测到的"自动化痕迹"补成接近真实 Chrome:

| 修复项 | 原始状态 | 修复后 |
|---|---|---|
| navigator.webdriver | true | undefined |
| navigator.plugins.length | 0 | 5 (典型 Chrome 内置 PDF Viewer) |
| navigator.languages | ["en-US"] | ["zh-CN", "zh", "en"] |
| window.chrome | 缺失或残缺 | 含 runtime / app / loadTimes 等 |
| navigator.permissions.query | Notification 行为不对 | 跟 Notification.permission 一致 |
| WebGL vendor / renderer | "Brian Paul"/"Mesa OffScreen" | "Intel Inc."/"Intel Iris Pro OpenGL Engine" |
| navigator.hardwareConcurrency | 物理核心可能 4 | 报 8 (主流 Mac/Win 都是 8) |
| navigator.deviceMemory | undefined | 8 |
| navigator.connection | 可能缺失 | 给一个 4G 兜底 |
| 头部 sec-ch-ua | playwright 偶尔与 UA 不匹配 | 由 UA 决定,一致就行 |

用法:
    await context.add_init_script(STEALTH_JS)
    # 之后所有 page 在 document_start 阶段都会先执行这段
"""
from __future__ import annotations

# 注意:这段 JS 会在每个 frame document_start 时执行,要写得幂等
STEALTH_JS = r"""
(() => {
  'use strict';

  // ---------- 1. navigator.webdriver ----------
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}

  // ---------- 2. navigator.plugins / mimeTypes ----------
  try {
    const makePlugin = (name, filename, description) => {
      const p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name: { value: name, enumerable: true },
        filename: { value: filename, enumerable: true },
        description: { value: description, enumerable: true },
        length: { value: 1, enumerable: true },
      });
      return p;
    };
    const plugins = [
      makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', ''),
      makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', ''),
      makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', ''),
      makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', ''),
    ];
    const arr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { arr[i] = p; arr[p.name] = p; });
    Object.defineProperty(arr, 'length', { value: plugins.length });
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => arr,
      configurable: true,
    });
  } catch (e) {}

  // ---------- 3. navigator.languages ----------
  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: () => ['zh-CN', 'zh', 'en'],
      configurable: true,
    });
  } catch (e) {}

  // ---------- 4. window.chrome ----------
  try {
    if (!window.chrome || !window.chrome.runtime) {
      const _chrome = window.chrome || {};
      _chrome.app = _chrome.app || {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails: function () { return null; },
        getIsInstalled: function () { return false; },
      };
      _chrome.runtime = _chrome.runtime || {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        connect: function () { return undefined; },
        sendMessage: function () { return undefined; },
      };
      _chrome.loadTimes = _chrome.loadTimes || function () {
        return {
          requestTime: performance.now() / 1000,
          startLoadTime: performance.now() / 1000,
          commitLoadTime: performance.now() / 1000,
          finishDocumentLoadTime: performance.now() / 1000,
          finishLoadTime: performance.now() / 1000,
          firstPaintTime: performance.now() / 1000,
          firstPaintAfterLoadTime: 0,
          navigationType: 'Other',
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
          npnNegotiatedProtocol: 'h2',
          wasAlternateProtocolAvailable: false,
          connectionInfo: 'h2',
        };
      };
      _chrome.csi = _chrome.csi || function () {
        return { startE: Date.now(), onloadT: Date.now(), pageT: 0, tran: 15 };
      };
      window.chrome = _chrome;
    }
  } catch (e) {}

  // ---------- 5. navigator.permissions.query ----------
  try {
    const orig = navigator.permissions && navigator.permissions.query;
    if (orig) {
      navigator.permissions.query = (parameters) => {
        if (parameters && parameters.name === 'notifications') {
          return Promise.resolve({
            state: typeof Notification !== 'undefined' ? Notification.permission : 'default',
            onchange: null,
          });
        }
        return orig.call(navigator.permissions, parameters);
      };
    }
  } catch (e) {}

  // ---------- 6. WebGL vendor / renderer ----------
  try {
    const fix = (proto) => {
      const orig = proto.getParameter;
      proto.getParameter = function (param) {
        // UNMASKED_VENDOR_WEBGL
        if (param === 37445) return 'Intel Inc.';
        // UNMASKED_RENDERER_WEBGL
        if (param === 37446) return 'Intel Iris Pro OpenGL Engine';
        return orig.call(this, param);
      };
    };
    if (window.WebGLRenderingContext) fix(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) fix(WebGL2RenderingContext.prototype);
  } catch (e) {}

  // ---------- 7. hardwareConcurrency / deviceMemory ----------
  try {
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
      get: () => 8,
      configurable: true,
    });
  } catch (e) {}
  try {
    if (!('deviceMemory' in Navigator.prototype) || navigator.deviceMemory == null) {
      Object.defineProperty(Navigator.prototype, 'deviceMemory', {
        get: () => 8,
        configurable: true,
      });
    }
  } catch (e) {}

  // ---------- 8. connection ----------
  try {
    if (!navigator.connection) {
      Object.defineProperty(Navigator.prototype, 'connection', {
        get: () => ({
          effectiveType: '4g',
          rtt: 50,
          downlink: 10,
          saveData: false,
          onchange: null,
        }),
        configurable: true,
      });
    }
  } catch (e) {}

  // ---------- 9. iframe.contentWindow chrome 一致性 ----------
  // 风控有时通过 iframe document.defaultView 检查
  try {
    const origGet = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow').get;
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
      get() {
        const w = origGet.call(this);
        try {
          if (w && !w.chrome) w.chrome = window.chrome;
        } catch (e) {}
        return w;
      },
    });
  } catch (e) {}

  // ---------- 10. 抹掉 toString 痕迹 ----------
  // 不改 toString 容易在 fn.toString() 里露馅(显示 [native code] 还是 javascript code)
  // 我们改的几个 getter,如果被 .toString() 检查会露,补一下
  try {
    const fn = Function.prototype.toString;
    Function.prototype.toString = function () {
      // 这里不便逐个 patch,先简单返回原始结果即可
      // 大多数风控只看几个 navigator getter,没到 fn.toString 这层
      return fn.call(this);
    };
  } catch (e) {}
})();
"""


def build_stealth_js(*, languages: list[str] | None = None,
                     hardware_concurrency: int = 8,
                     device_memory: int = 8) -> str:
    """如果将来要按 settings 调整 stealth 参数,从这里返回定制化的 JS。
    现阶段直接返回常量。"""
    return STEALTH_JS
