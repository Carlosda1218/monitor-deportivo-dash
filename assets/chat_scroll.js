/* Auto-scroll chat messages to bottom whenever the list changes */
(function () {
  var _messagesObserver = null;
  var _pageObserver = null;
  var _current = null;

  function scrollBottom(el) {
    if (el) el.scrollTop = el.scrollHeight;
  }

  function attachObserver(el) {
    if (!el || el === _current) return;
    if (_messagesObserver) _messagesObserver.disconnect();
    _current = el;
    _messagesObserver = new MutationObserver(function () { scrollBottom(el); });
    _messagesObserver.observe(el, { childList: true, subtree: false });
    scrollBottom(el);
  }

  function scan() {
    var el = document.getElementById("chat-messages");
    if (el) {
      attachObserver(el);
      return;
    }
    if (_current && _messagesObserver) {
      _messagesObserver.disconnect();
      _messagesObserver = null;
      _current = null;
    }
  }

  function init() {
    if (!document.body) return;
    scan();
    if (_pageObserver) return;
    _pageObserver = new MutationObserver(scan);
    _pageObserver.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
