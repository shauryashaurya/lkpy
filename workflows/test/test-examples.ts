import { WorkflowJob } from "@lenskit/typeline/github";
import { checkoutStep } from "../lib/checkout.ts";
import { condaSetup, CondaTestOpts } from "./conda.ts";
import { mlDataSteps } from "./data.ts";
import { coverageSteps } from "./common.ts";
import { script } from "../lib/script.ts";
import { PACKAGES } from "../lib/defs.ts";

export function exampleTestJob(): WorkflowJob {
  const options: CondaTestOpts = {
    install: "conda",
    key: "examples",
    name: "Demos, examples, and docs",
    pixi_env: "test-examples",
    packages: PACKAGES,
  };

  const cov = PACKAGES.map((pkg) => `--cov=${pkg}/lenskit`).join(" ");
  return {
    name: options.name,
    "runs-on": "ubuntu-latest",
    steps: [
      checkoutStep(),
      ...condaSetup(options),
      ...mlDataSteps(["ml-100k", "ml-1m", "ml-10m", "ml-20m"]),
      {
        "name": "📕 Validate code examples",
        "run": script(
          `sphinx-build -b doctest docs build/doc`,
        ),
      },
      {
        "name": "📕 Validate example notebooks",
        "run": script(
          `pytest ${cov} --nbval-lax --log-file test-notebooks.log docs`,
        ),
      },
      ...coverageSteps(options),
    ],
  };
}
