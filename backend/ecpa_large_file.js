// GitHub omits inline base64 for files above 1 MB. Read the same immutable blob.
function ecpaFileBytes_(data, path, head) {
  if (typeof data.content === 'string' && data.encoding !== 'none')
    return Utilities.base64Decode(data.content);
  if (!data || !/^[a-f0-9]{40}$/.test(data.sha || '')) throw Error('Invalid GitHub file metadata');
  const headers = Object.assign({}, githubHeaders(), {Accept:'application/vnd.github.raw+json'});
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + GITHUB_REPO + '/git/blobs/' + data.sha,
    {headers, muteHttpExceptions:true});
  if (r.getResponseCode() !== 200) throw Error('GitHub large file read failed');
  return r.getBlob().getBytes();
}
