#include ./scripts/env.sh

workspace:
	python3 -m venv env
	source env/bin/activate
	python3 -m pip install -r requirements.txt
	#pytest
###################### DOCKER BASE ######################

TARGET_ENV_FILE=env_docker
#TARGET_ENV_FILE=env_prod_docker

docker_build:
	docker build -t gptinslack .

docker: docker_build
	docker run --env SERVER_ROLE=webapp --env-file ./scripts/$(TARGET_ENV_FILE).sh -it -p 9891:9891 gptinslack


###################### HELM ######################
helm:
	helm package --app-version=latest helm/gptinslack
	helm upgrade --install gptinslack -f helm/gptinslack/values.dev.yaml ./gptinslack-0.1.0.tgz --set gptinslack.tag=latest --namespace dev

helm-d:
	helm uninstall gptinslack -n dev

helm_restart:
	kubectl rollout restart deployment gptinslack -n dev

setpass:
	export ANSIBLE_VAULT_PASSWORD_FILE=${PWD}/.vault_password.txt

encrypt:
	#ansible-vault encrypt ${PWD}/scripts/env_docker.sh
	#ansible-vault encrypt ${PWD}/scripts/env.sh

decrypt:
	#ansible-vault decrypt ${PWD}/scripts/env_docker.sh
	#ansible-vault decrypt ${PWD}/scripts/env.sh


.PHONY: docker_build docker
