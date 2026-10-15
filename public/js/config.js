// The only place for deployment- and name-dependent values.
// The release is read from this module's own URL, which the import map in index.html versions.
const release = new URL(import.meta.url).searchParams.get('v');

export const CONFIG = {
  release,
  productName: null,
  storagePrefix: 'follow.v0',
  dataPath: 'data/',
  i18nPath: 'i18n/',
  defaultLocale: 'en',
  licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/',
  repoUrl: 'https://github.com/sagosand-solutions/follow',
};
