########################################
## Part 1 - окружение и Инсталляция  ###
########################################
> Перечислить IP-адреса виртуальных машин и установленную ОС. 
> Предоставить bash-скрипт, который нужно выполнить на host-машине, 
> чтобы запустить облако.
     
    echo 'смотрим окружение'   
    lsb_release -a

>  'мой-хост№1: Ubuntu 14.04.5 LTS (GNU/Linux 3.13.0-110-generic x86_64)'

    echo 'запоминаем ip:'
    hostname --all-ip-addresses
> 'ip моего хоста: IP address for eth0: 90.156.141.158'

    echo 'Ставим docker из репозитария'
    sudo apt-get -y install docker-engine

    echo 'Ставим docker composer'
    cd /opt
    sudo curl -L "https://github.com/docker/compose/releases/download/1.11.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose

    echo 'Клонируем репозиторий для теста '
    cd /opt
    sudo git clone https://github.com/tarantool/cloud.git


##################################
##Part2 - check ##
##################################
> Как убедиться, что облако работает? Например, зайти по такому-то адресу, посмотреть в лог и пр.

    echo 'Посмотрим запущенные контейнеры, занятые порты, и ошибки docker'
    docker ps | grep tarantool-cloud
    netstat -apn| grep LISTENING
    cat /var/log/upstart/docker.log | grep error
     
    echo 'Запустилась ли web UI?'
    curl -v http://localhost:5061
    
    echo 'Смотрим в браузере: http://localhost:5061 '
    lynx http://localhost:5061'


############################
## Part 3 - первый хост 90.156.141.158  ##
############################

>  Предоставить bash-скрипт, который через CLI (taas)
> создает одну логическую ноду в кластере, 
> коннектится к ней, 
> создает в ней space и 
> делает три любых записи



  

**Запускаем *tarantool* и создaем кластер *GE* с пользователем *replicator***



    box.cfg{listen = 3301,
    logger = docker.log}
    
    box.schema.user.create('replicator', {password = 'password'})
    box.schema.user.grant('replicator','execute','role','replication')
    box.space._cluster:select({0}, {iterator = 'GE'})



#######################
#Part 3 -второй хост 172.31.27.169##
#######################

>  подключаем хост2 *ip=172.31.27.169* в кластер и настраиваем репликацию  

    box.cfg{
      listen = 3302,
      logger = docker.log,
      replication_source = 'replicator:password@90.156.141.158:3301'
    }
    box.space._cluster:select({0}, {iterator = 'GE'})

Должен появиться id (sha256) первого хоста

#######################
#Part 4 -Replication ##
#######################


> Результаты host1:cmd3 и host2:cmd3 должны совпадать и должны быть такими-то



**проверим репликацию введя некоторые данные в tarantool:**



    host1:box.schema.space.create('tarantools')
    host1:box.space.tarantools:create_index('primary', {type = 'tree', parts = {1, 'UNSIGNED'}})
    host1:box.space.tarantools.select(box.space.tarantools,{})
    
    host1:box.space.tarantools:auto_increment{'Lycosa', 'Lycosa anclata'}
    
    host2:box.space.tarantools:auto_increment{'Lycosa', 'Lycosa accurata'}
    
    host1:box.space.tarantools:select{}
 должны высветится два элемента 

##################
#Part 5 -хост 3 ##
##################
> Добавить третью виртуальную машину в кластер. Назовем её host3


    box.cfg{ listen = 3302,
    	logger = docker.log,
    	replication_source = {replicator:password@90.156.141.158:3301}, {replicator:password@172.31.27.169:3302} }
    box.space._cluster:select({0}, {iterator = 'GE'})

##################
#Part 6 - логи  ##
##################

    find -f docker.log


########################
#Part 7 создаем ноду ##
########################
>  'создаем ноду'

    ./taas -H localhost:5061 run --name myinstance


----------


# Полезные Комманды: #
    docker run --name mytarantool -d tarantool/tarantool:1.7
     docker run --name wtf -d -p 3302:3301 -v /opt/cloud/data:/var/lib/tarantool tarantool/tarantool

    export TARANTOOL_CLOUD_HOST=localhost:5061
    
    echo 'working instances:'
    ./taas  -H localhost:5061 ps
    
    echo 'create group'
    ./taas -H localhost:5061 run


#Баг  в taas
> too many attemts if not connected to docker
