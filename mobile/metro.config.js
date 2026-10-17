// The phone shares its protocol code with the Mac link and the relay (../companion/protocol), so Metro watches that
// folder too and resolves its imports (tweetnacl) from this app's node_modules.
const {getDefaultConfig}=require('expo/metro-config');
const path=require('path');

const config=getDefaultConfig(__dirname);
const companion=path.resolve(__dirname,'../companion/protocol');
config.watchFolders=[...(config.watchFolders??[]),companion];
config.resolver.nodeModulesPaths=[path.resolve(__dirname,'node_modules')];
module.exports=config;
