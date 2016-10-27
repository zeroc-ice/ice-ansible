Vagrant.configure(2) do |config|
  config.vm.box = 'ubuntu/xenial64'

  config.vm.provision "shell", inline: <<-EOS
    apt-get install -y python python-pip libbz2-dev libssl-dev
    python -m pip install --upgrade pip
    python -m pip install zeroc-ice
  EOS

  config.vm.provision :ansible do |ansible|
    # ansible.inventory_path = "tests/"
    ansible.playbook = 'tests/playbook.yml'
  end
end
