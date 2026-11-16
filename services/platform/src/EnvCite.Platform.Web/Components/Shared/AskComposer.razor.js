// 互動輸入框的內容以 DOM 為準，元件不在每次重繪把 value 寫回去。
// 程式自己改內容（送出後清空、帶入一句話）時才從這裡寫進 DOM。
export function setValue(element, value) {
    if (!(element instanceof HTMLTextAreaElement) || element.value === value) {
        return;
    }

    element.value = value;
    const end = value.length;
    element.setSelectionRange(end, end);
}
