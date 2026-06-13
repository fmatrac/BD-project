// Switch the active user while keeping the current page's other query params
// (filters, pagination). Used by the "Acting as" dropdown in the top bar.
function setUser(userId) {
  const url = new URL(window.location.href);
  if (userId) {
    url.searchParams.set("user_id", userId);
  } else {
    url.searchParams.delete("user_id");
  }
  window.location.href = url.toString();
}
