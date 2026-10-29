// The host page's own script: language, group and an optional house style from the query,
// so one page covers every widget state in pages.json. Deferred, before the widget's module:
// the element exists and has its attributes before the widget starts.
(function () {
  var params = new URLSearchParams(window.location.search);
  document.documentElement.lang = params.get('lang') || 'nl';
  if (params.get('theme') === 'gemeente') {
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/host.css';
    document.head.append(link);
  }
  var widget = document.querySelector('gov-booking');
  if (params.get('organisation')) widget.setAttribute('organisation', params.get('organisation'));
  if (params.get('group')) widget.setAttribute('group', params.get('group'));
})();
