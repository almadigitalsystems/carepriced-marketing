// CarePriced GA4 Analytics — G-CTS218PNZ3
// Tracks: pageviews, form_submission, affiliate_click
(function() {
  var GA_ID = 'G-CTS218PNZ3';

  // GA4 base setup
  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag('js', new Date());
  gtag('config', GA_ID);

  // Load gtag.js async
  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_ID;
  document.head.appendChild(s);

  // ── form_submission event ──
  // Call window.cpTrackFormSubmission() after successful intake form POST
  window.cpTrackFormSubmission = function() {
    gtag('event', 'form_submission', {
      event_category: 'intake',
      page_slug: window.location.pathname.replace(/\/$/, '') || '/'
    });
  };

  // ── affiliate_click event ──
  // Automatically tracks clicks on links with data-affiliate attribute
  // Example: <a href="..." data-affiliate="aplaceformom">A Place for Mom</a>
  document.addEventListener('click', function(e) {
    var link = e.target.closest ? e.target.closest('a[data-affiliate]') : null;
    if (!link) return;
    var affiliateName = link.getAttribute('data-affiliate');
    var pageSlug = window.location.pathname.replace(/^\//, '').replace(/\/$/, '') || 'home';
    gtag('event', 'affiliate_click', {
      affiliate_name: affiliateName,
      page_slug: pageSlug
    });
  });
})();
