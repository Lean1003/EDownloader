document.addEventListener('DOMContentLoaded', () => {
    const toggleSwitch = document.getElementById('toggleSwitch');
    const dataSection = document.getElementById('data-section');
    const noDataSection = document.getElementById('no-data-section');
    const caughtUrlEl = document.getElementById('caughtUrl');
    const downloadBtn = document.getElementById('downloadBtn');

    // Xóa huy hiệu '!' trên icon khi người dùng mở popup
    chrome.action.setBadgeText({ text: '' });

    // Cập nhật trạng thái nút bật/tắt
    chrome.storage.local.get({ isEnabled: true }, (result) => {
        toggleSwitch.checked = result.isEnabled;
    });

    toggleSwitch.addEventListener('change', () => {
        chrome.storage.local.set({ isEnabled: toggleSwitch.checked });
    });

    // === PHẦN QUAN TRỌNG: KIỂM TRA DỮ LIỆU VÀ KÍCH HOẠT NÚT TẢI XUỐNG ===
    chrome.storage.local.get('lastCaughtData', (result) => {
        const caughtData = result.lastCaughtData;

        // Nếu có dữ liệu trong bộ nhớ
        if (caughtData && caughtData.data) {
            // Hiển thị khu vực download và ẩn thông báo
            dataSection.classList.remove('hidden');
            noDataSection.classList.add('hidden');
            
            // Hiển thị URL đã bắt được
            caughtUrlEl.textContent = caughtData.url;

            // Gắn sự kiện "click" cho nút download
            downloadBtn.addEventListener('click', () => {
                // 1. Chuyển object JSON thành chuỗi text, định dạng đẹp (cách 2 space)
                const jsonString = JSON.stringify(caughtData.data, null, 2);
                
                // 2. Tạo một Blob (Binary Large Object) từ chuỗi JSON
                const blob = new Blob([jsonString], { type: 'application/json' });
                
                // 3. Tạo một URL tạm thời cho Blob
                const url = URL.createObjectURL(blob);

                // 4. Trích xuất "slug" từ URL để đặt tên file cho đẹp
                const urlParts = caughtData.url.split('/');
                const slug = urlParts[urlParts.length - 1] || urlParts[urlParts.length - 2] || "data";
                const filename = `${slug}.json`;

                // 5. Sử dụng API chrome.downloads để tải file
                chrome.downloads.download({
                    url: url,
                    filename: filename,
                    saveAs: true // Hiện hộp thoại "Lưu thành..."
                });
            });
        } else {
            // Nếu không có dữ liệu, ẩn khu vực download
            dataSection.classList.add('hidden');
            noDataSection.classList.remove('hidden');
        }
    });
});