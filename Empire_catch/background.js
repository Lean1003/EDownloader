// --- CẤU HÌNH VÀ BIẾN TOÀN CỤC ---
const DEBUGGER_VERSION = "1.3";
const TARGET_API_PREFIX = "https://api.empire.io.vn/api/v1/courses/";
const DEBUG_TARGET_DOMAIN = "empire.edu.vn";

const attachedTabs = new Set();
const interestingRequests = {};

// --- CÁC HÀM QUẢN LÝ DEBUGGER ---
async function attachDebugger(tabId) {
    if (attachedTabs.has(tabId)) return;
    try {
        await chrome.debugger.attach({ tabId }, DEBUGGER_VERSION);
        await chrome.debugger.sendCommand({ tabId }, "Network.enable");
        attachedTabs.add(tabId);
        console.log(`[Debugger] Gắn thành công vào tab ${tabId} trên domain ${DEBUG_TARGET_DOMAIN}`);
    } catch (error) {
        console.warn(`[Debugger] Không thể gắn vào tab ${tabId}: ${error.message}`);
    }
}

async function detachDebugger(tabId) {
    if (!attachedTabs.has(tabId)) return;
    try {
        await chrome.debugger.detach({ tabId });
    } catch (error) { /* Lỗi thường xảy ra nếu tab đã đóng, có thể bỏ qua */ } 
    finally {
        attachedTabs.delete(tabId);
        console.log(`[Debugger] Gỡ khỏi tab ${tabId}`);
    }
}

// Hàm bật/tắt chính dựa trên trạng thái
async function setExtensionState(isEnabled) {
    if (isEnabled) {
        console.log(`[Debugger] Bật extension. Tìm và gắn vào các tab trên ${DEBUG_TARGET_DOMAIN}...`);
        const tabs = await chrome.tabs.query({ url: `*://*.${DEBUG_TARGET_DOMAIN}/*` });
        for (const tab of tabs) {
            attachDebugger(tab.id);
        }
    } else {
        console.log('[Debugger] Tắt extension. Gỡ khỏi tất cả các tab...');
        const tabsToDetach = [...attachedTabs];
        for (const tabId of tabsToDetach) {
            detachDebugger(tabId);
        }
    }
}

// --- CÁC EVENT LISTENER ĐỂ QUẢN LÝ VÒNG ĐỜI ---
chrome.runtime.onInstalled.addListener(async () => {
    await chrome.storage.local.set({ isEnabled: true });
    setExtensionState(true);
});

chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes.isEnabled) {
        setExtensionState(changes.isEnabled.newValue);
    }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
    if (changeInfo.status !== 'complete' || !tab.url) return;
    
    const { isEnabled } = await chrome.storage.local.get({ isEnabled: true });
    if (!isEnabled) return;

    if (tab.url.includes(DEBUG_TARGET_DOMAIN)) {
        attachDebugger(tabId);
    } else {
        detachDebugger(tabId);
    }
});

chrome.tabs.onRemoved.addListener(detachDebugger);
chrome.debugger.onDetach.addListener(source => attachedTabs.delete(source.tabId));

// --- LẮNG NGHE SỰ KIỆN MẠNG ---
chrome.debugger.onEvent.addListener(async (source, method, params) => {
    if (!attachedTabs.has(source.tabId)) return;

    if (method === "Network.responseReceived" && params.response?.url?.startsWith(TARGET_API_PREFIX)) {
        console.log(`[Debugger] Phát hiện API: ${params.response.url}. Đang chờ tải xong...`);
        interestingRequests[params.requestId] = { url: params.response.url, tabId: source.tabId };
    }

    if (method === "Network.loadingFinished" && interestingRequests[params.requestId]) {
        const { url, tabId } = interestingRequests[params.requestId];
        console.log(`[Debugger] Đã tải xong API. Bắt đầu lấy nội dung từ ${url}`);
        
        try {
            const { body } = await chrome.debugger.sendCommand({ tabId }, "Network.getResponseBody", { requestId: params.requestId });
            
            if (body) {
                const data = JSON.parse(body);
                console.log("%c[Empire Catcher] Dữ liệu JSON nhận được:", 'color: blue; font-weight: bold;', data);

                await chrome.storage.local.set({
                    lastCaughtData: { url, data, timestamp: new Date().getTime() }
                });
                
                chrome.action.setBadgeText({ text: '!' });
                chrome.action.setBadgeBackgroundColor({ color: '#F44336' });
            }
        } catch (error) {
            console.error(`[Debugger] Lỗi khi lấy nội dung response từ ${url}:`, error.message);
        } finally {
            delete interestingRequests[params.requestId];
        }
    }
});