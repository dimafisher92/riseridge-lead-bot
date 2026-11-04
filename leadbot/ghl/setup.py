"""Creates what is missing, verifies the rest. Never deletes or retypes."""

from dataclasses import dataclass, field


@dataclass
class SetupReport:
    created: list = field(default_factory=list)
    existing: list = field(default_factory=list)
    drift: list = field(default_factory=list)
    missing_ui: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.drift and not self.missing_ui


def run_setup(client, schema, *, dry_run=False, verify_only=False):
    report = SetupReport()
    write = not (dry_run or verify_only)

    have_tags = {t["name"].lower() for t in client.list_tags()}
    for tag in schema.all_tags():
        if tag.lower() in have_tags:
            report.existing.append(f"tag {tag}")
        elif verify_only:
            report.drift.append(f"tag {tag} is missing")
        else:
            if write:
                client.create_tag(tag)
            report.created.append(f"tag {tag}")

    have_fields = {f["name"]: f for f in client.list_custom_fields()}
    for spec in schema.fields:
        found = have_fields.get(spec.name)
        if found is None:
            if spec.created_by == "ui":
                report.missing_ui.append(spec.name)
            elif verify_only:
                report.drift.append(f"field {spec.name} is missing")
            else:
                if write:
                    client.create_custom_field(spec.name, spec.data_type)
                report.created.append(f"field {spec.name}")
            continue
        report.existing.append(f"field {spec.name}")
        if found.get("dataType") != spec.data_type:
            report.drift.append(
                f"field {spec.name} is {found.get('dataType')}, expected {spec.data_type}")
        missing = [o for o in spec.options if o not in (found.get("picklistOptions") or [])]
        if missing:
            report.drift.append(f"field {spec.name} lacks options: {', '.join(missing)}")

    pipeline = next((p for p in client.list_pipelines()
                     if p["name"].lower() == schema.pipeline_name.lower()), None)
    if pipeline is None:
        if verify_only:
            report.drift.append(f"pipeline {schema.pipeline_name} is missing")
        else:
            if write:
                client.create_pipeline(schema.pipeline_name, list(schema.stages))
            report.created.append(f"pipeline {schema.pipeline_name}")
    else:
        report.existing.append(f"pipeline {schema.pipeline_name}")
        names = tuple(s["name"] for s in pipeline.get("stages", []))
        if names != schema.stages:
            report.drift.append(
                f"pipeline {schema.pipeline_name} stages are {list(names)}, "
                f"expected {list(schema.stages)}")
    return report
