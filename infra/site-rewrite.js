// Directory-index rewriting for the static export.
//
// The origin is the S3 REST endpoint, not the website endpoint, so it has no
// directory-index behaviour of its own, and default_root_object only covers
// "/". Without this, "/players/" is fetched as the key "players/", which does
// not exist — and since the bucket policy grants no s3:ListBucket, S3 answers
// 403 AccessDenied rather than 404, so the raw XML reaches the browser.
//
// next.config.mjs sets trailingSlash, so the canonical form is "/players/".
function handler(event) {
  var request = event.request;
  var uri = request.uri;

  if (uri.endsWith("/")) {
    request.uri = uri + "index.html";
    return request;
  }

  // No dot in the last segment => a route, not an asset. Redirect rather than
  // rewrite, so the canonical trailing-slash URL is the one users keep.
  var last = uri.slice(uri.lastIndexOf("/") + 1);
  if (last.indexOf(".") === -1) {
    var qs = [];
    for (var key in request.querystring) {
      var value = request.querystring[key].value;
      qs.push(value ? key + "=" + value : key);
    }
    return {
      statusCode: 301,
      statusDescription: "Moved Permanently",
      headers: {
        location: {
          value: uri + "/" + (qs.length ? "?" + qs.join("&") : ""),
        },
      },
    };
  }

  // Assets (/_next/static/*.js, *.css) and the RSC payloads (*.txt) all have a
  // dot in the last segment, so they pass through untouched.
  return request;
}
