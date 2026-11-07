# Amplifier shell SDK

TypeScript authors can annotate the factory's host-provided React type;
`defineModule` preserves that type and the returned component signature. The
package qualification compiles a strict installed consumer and checks that
unsupported commands, snapshot mutation and invalid factories remain errors.

`@amplifier/shell-sdk` contains dependency-free helpers and TypeScript declarations
for independently authored navigation, Canvas renderers and shell components.
Package version `1.0.0` describes the existing host interface `apiVersion: "1.0"`.
It does not version the application or declare compatibility with future APIs.

The package currently ships from the Unified repository. A public npm release
and separate repository are not yet established. Build an installable artifact
from this directory with `npm pack --pack-destination /path/to/artifacts`, then
install that tarball in your component project:

```sh
npm install /path/to/artifacts/amplifier-shell-sdk-1.0.0.tgz
```

Import the package by name and accept React from the host:

```js
import {defineModule, useShellComponent} from '@amplifier/shell-sdk';

export default defineModule(({React}) => function Component({host}) {
  const snapshot = useShellComponent(React, host);
  return React.createElement('span', null, snapshot.slot);
});
```

Bundle the SDK into a single browser ESM artifact. Do not bundle another React,
import private Unified modules, or substitute a local conversation store for
the host snapshot. `useNavigation` and `useCanvas` subscribe to their respective
host interfaces in the same way. The included declarations describe each host.

The host owns manifest compatibility, capability discovery, validation and
activation. A successful build is not acceptance: discover the installed schema,
stage the manifest and artifact, validate, preview/apply and verify in the target
client. Native extensions run as trusted same-origin code; this SDK is not an
isolation boundary. Keep dirty-edit, generation, recovery and action-receipt
semantics intact.

See the [component contract](https://github.com/microsoft/amplifier-unified/blob/main/docs/shell/components.md),
[navigation and renderer contracts](https://github.com/microsoft/amplifier-unified/blob/main/docs/shell/README.md),
and [standalone controls example](https://github.com/microsoft/amplifier-unified/tree/main/examples/shell-controls).
Those host contracts remain canonical; the SDK does not duplicate their validator.
