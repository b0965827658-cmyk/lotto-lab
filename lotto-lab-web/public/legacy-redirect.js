(function redirectLegacyTool() {
  'use strict';
  const game = new URLSearchParams(location.search).get('game');
  location.replace(`/#${game === 'ca-fantasy5' ? 'fantasy5' : 'tw539'}`);
})();
