"""Metadata-only dependency qualification, runnable in an older app candidate."""

from importlib import metadata

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def installed_dependency_extras(app_extras):
    """Carry activated extras into exact overrides, which otherwise erase them.

    Feature addition retains this same application and every installed package.
    Follow their metadata, including transitive extras and environment markers;
    a newly requested package that is absent has no installed pin to override.
    An absent intermediary may request an extra of an installed downstream
    package that this metadata cannot discover. The candidate dependency probe
    verifies the complete resolved closure and rejects that incomplete install.
    """
    rows = {canonicalize_name(d.metadata['Name']): d for d in metadata.distributions()}
    if 'amplifier-unified' not in rows:
        raise ValueError('The installed application dependency metadata is missing.')
    active = {name: {''} for name in rows}
    active['amplifier-unified'].update(map(canonicalize_name, app_extras))
    checked = set()
    while pending := [(name, extra) for name, extras in active.items() for extra in extras
                      if (name, extra) not in checked]:
        for name, extra in pending:
            checked.add((name, extra))
            distribution = rows.get(name)
            if distribution is None:
                continue
            environment = {**default_environment(), 'extra': extra}
            for value in distribution.requires or []:
                requirement = Requirement(value)
                if requirement.marker and not requirement.marker.evaluate(environment):
                    continue
                target = canonicalize_name(requirement.name)
                active.setdefault(target, {''}).update(map(canonicalize_name, requirement.extras))
    return {name: sorted(extras - {''}) for name, extras in active.items() if extras - {''}}

# Overrides retain exact installed Git revisions even when declarations track
# main. Independently check version constraints, including activated transitive
# extras, so overrides cannot hide an incompatible additive feature.
DEPENDENCY_PROBE = r'''import importlib.metadata as m,sys
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.markers import default_environment
rows={canonicalize_name(d.metadata['Name']):d for d in m.distributions()}
active={name:{''} for name in rows}
active['amplifier-unified'].update(sys.argv[1:])
checked=set()
while True:
 pending=[(name,extra) for name,extras in active.items() for extra in extras if (name,extra) not in checked]
 if not pending:break
 for name,extra in pending:
  checked.add((name,extra))
  environment={**default_environment(),'extra':extra}
  for value in rows[name].requires or []:
   requirement=Requirement(value)
   if requirement.marker and not requirement.marker.evaluate(environment):continue
   target=canonicalize_name(requirement.name)
   if target not in rows or not requirement.specifier.contains(rows[target].version,prereleases=True):
    raise ValueError('The optional feature conflicts with the preserved dependency environment.')
   active[target].update(requirement.extras)
print('Feature dependency metadata is compatible.')
'''
