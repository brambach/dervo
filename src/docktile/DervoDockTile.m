// Dervo's Dock tile plug-in (beta.15, group 7). macOS has no alternate-icon switch like iOS: a running app can set its
// Dock icon, but that resets when it quits. The Dock loads this bundle from Contents/PlugIns (named by NSDockTilePlugIn in
// the app's Info.plist) while Dervo is in the Dock, so the icon you chose stays there while Dervo is closed. Finder,
// Spotlight and Launchpad keep the bundled icon.
//
// The choice is one short id in Dervo's own preferences (src/app_icon.rs writes it after the host has checked it). This
// draws the matching <id>.icns from its own Resources, and nothing when the id is Cream, unknown or missing, so the
// Dock shows the bundled icon. It reads nothing else and writes nothing.
#import <Cocoa/Cocoa.h>

static NSString *const DervoDomain = @"local.dervo.connected";
static NSString *const DervoIconKey = @"DervoAppIcon";
static NSString *const DervoIconChanged = @"local.dervo.connected.appIconChanged";

@interface DervoDockTile : NSObject <NSDockTilePlugIn>
@property (strong) NSDockTile *tile;
@property (strong) id observer;
@end

@implementation DervoDockTile

static BOOL DervoIconId(NSString *value) {
    if (![value isKindOfClass:NSString.class] || value.length == 0 || value.length > 20) return NO;
    NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:@"abcdefghijklmnopqrstuvwxyz"];
    return [value rangeOfCharacterFromSet:allowed.invertedSet].location == NSNotFound;
}

- (void)setDockTile:(NSDockTile *)dockTile {
    if (self.observer) {
        [[NSDistributedNotificationCenter defaultCenter] removeObserver:self.observer];
        self.observer = nil;
    }
    self.tile = dockTile;
    if (!dockTile) return;
    __weak DervoDockTile *weak = self;
    self.observer = [[NSDistributedNotificationCenter defaultCenter] addObserverForName:DervoIconChanged object:nil
        queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note) { [weak update]; }];
    [self update];
}

- (void)update {
    NSDockTile *tile = self.tile;
    if (!tile) return;
    CFPreferencesAppSynchronize((__bridge CFStringRef)DervoDomain);
    id choice = CFBridgingRelease(CFPreferencesCopyAppValue((__bridge CFStringRef)DervoIconKey, (__bridge CFStringRef)DervoDomain));
    NSImage *image = nil;
    if (DervoIconId(choice) && ![choice isEqualToString:@"cream"]) {
        NSURL *url = [[NSBundle bundleForClass:self.class] URLForResource:choice withExtension:@"icns"];
        if (url) image = [[NSImage alloc] initWithContentsOfURL:url];
    }
    if (image) {
        NSImageView *view = [[NSImageView alloc] initWithFrame:NSMakeRect(0, 0, tile.size.width, tile.size.height)];
        view.image = image;
        view.imageScaling = NSImageScaleProportionallyUpOrDown;
        tile.contentView = view;
    } else {
        tile.contentView = nil;
    }
    [tile display];
}

- (void)dealloc {
    if (_observer) [[NSDistributedNotificationCenter defaultCenter] removeObserver:_observer];
}

@end
